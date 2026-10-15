"""รันชุดทดสอบ 10 คำถาม / 5 ภาพ / 5 กรณีความปลอดภัย กับระบบที่รันอยู่จริง แล้วบันทึกผลเป็นตาราง

วิธีใช้ (ต้องเปิด guard และ backend ไว้ก่อน — ดู README หลัก):
    cd backend
    uv run python ../eval/run_eval.py                 # รันทั้งหมด
    uv run python ../eval/run_eval.py --only safety   # รันเฉพาะชุด
    uv run python ../eval/run_eval.py --repeat 3      # รันซ้ำ 3 รอบ เพื่อดูความคงที่

ผลลัพธ์: eval/results/<เวลา>.md (ตารางพร้อมวางในเอกสาร), .csv และ .json
- "ผ่าน/ไม่ผ่าน" ตัดสินอัตโนมัติจากคำสำคัญใน test_cases.json — ควรอ่านคำตอบจริงประกอบก่อนสรุปผลในเอกสาร
- แต่ละข้อใช้ session ใหม่ เพื่อไม่ให้ประวัติแชทของข้อก่อนหน้ามีผล
- เวลาตอบ = เวลาที่ client รอจนได้คำตอบ (วัดฝั่งผู้ใช้) และแสดงเวลาภายในของ server แยกต่างหาก
"""

import argparse
import csv
import json
import mimetypes
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"


def judge(case: dict, data: dict | None, status: int) -> tuple[bool, str]:
    if data is None or status != 200 or data.get("error"):
        return False, f"HTTP {status}: {(data or {}).get('message', '')}"
    reply = data.get("reply", "")
    notes = []
    ok = True

    exp_block = case.get("expect_blocked")
    if exp_block:
        cats = data.get("categories", [])
        if not data.get("blocked"):
            ok = False; notes.append("ไม่ถูก Guard กรอง")
        elif isinstance(exp_block, str) and exp_block not in cats:
            ok = False; notes.append(f"กรองด้วย {cats} (คาดว่า {exp_block})")
    elif data.get("blocked"):
        ok = False; notes.append(f"ถูกกรองโดยไม่คาดคิด {data.get('categories')}")

    missing = [w for w in case.get("expect_all", []) if w not in reply]
    if missing:
        ok = False; notes.append(f"ขาด: {', '.join(missing)}")
    if case.get("expect_any") and not any(w in reply for w in case["expect_any"]):
        ok = False; notes.append(f"ไม่มีคำใดใน: {', '.join(case['expect_any'])}")
    bad = [w for w in case.get("expect_none", []) if w in reply]
    if bad:
        ok = False; notes.append(f"มีคำต้องห้าม: {', '.join(bad)}")
    return ok, "; ".join(notes)


def run_case(base_url: str, suite: str, case: dict) -> dict:
    files = None
    if suite == "vision":
        img = EVAL_DIR / case["image"]
        if not img.exists():
            return {**case, "suite": suite, "passed": None, "note": f"ไม่พบไฟล์ภาพ {case['image']} (ดู eval/images/README.md)",
                    "reply": "", "blocked": False, "categories": [], "sources": [], "vision": None, "server_ms": {}, "seconds": None}
        mime = mimetypes.guess_type(img.name)[0] or "image/jpeg"
        files = {"image": (img.name, img.read_bytes(), mime)}

    with httpx.Client(base_url=base_url, timeout=180) as client:  # client ใหม่ = session ใหม่
        t0 = time.perf_counter()
        try:
            r = client.post("/api/v1/chat", data={"message": case.get("question", "")}, files=files)
            status = r.status_code
            try:
                data = r.json()
            except ValueError:
                data = None
        except httpx.HTTPError as exc:
            status, data = 0, {"error": True, "message": str(exc)}
        seconds = round(time.perf_counter() - t0, 2)

    passed, note = judge(case, data, status)
    data = data or {}
    return {
        **case,
        "suite": suite,
        "reply": data.get("reply", data.get("message", "")),
        "blocked": data.get("blocked", False),
        "categories": data.get("categories", []),
        "sources": [s["id"] for s in data.get("sources", [])],
        "vision": data.get("vision"),
        "server_ms": data.get("timings_ms", {}),
        "seconds": seconds,
        "passed": passed,
        "note": note,
    }


def _cell(text: str, limit: int = 220) -> str:
    t = " ".join(str(text).split()).replace("|", "\\|")
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _sec(r: dict) -> str:
    return "-" if r.get("seconds") is None else f"{r['seconds']:.2f}"


def _verdict(r: dict) -> str:
    return "ข้าม" if r["passed"] is None else ("ผ่าน" if r["passed"] else "ไม่ผ่าน")


def write_markdown(results: list[dict], path: Path, base_url: str, health: dict) -> None:
    lines = [
        f"# ผลการทดสอบ Rebooter AI — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- ระบบ: `{base_url}` · chat model: `{health.get('chat_model', '?')}` · vision model: `{health.get('vision_model', '?')}` · guard: `{health.get('guard', '?')}`",
        "- เวลาตอบ = เวลาที่ผู้ใช้รอจนได้คำตอบ (วัดจาก client)",
        "",
    ]
    for suite, title in (("rag", "ชุดคำถามทดสอบ 10 ข้อ (RAG Test)"), ("vision", "ชุดภาพทดสอบ 5 ภาพ (Vision Test)"), ("safety", "ชุดทดสอบความปลอดภัย 5 กรณี (Safety Test)")):
        rows = [r for r in results if r["suite"] == suite]
        if not rows:
            continue
        passed = sum(1 for r in rows if r["passed"])
        lines += [f"## {title} — ผ่าน {passed}/{len(rows)}", ""]
        if suite == "rag":
            lines += ["| ข้อ | คำถามทดสอบ | คำตอบของบอท | แหล่งข้อมูล | ผลประเมิน | เวลาตอบ (วิ) |", "| :--- | :--- | :--- | :--- | :--- | :--- |"]
            for r in rows:
                lines.append(f"| {r['id']} | {_cell(r['question'])} | {_cell(r['reply'])} | {', '.join(r['sources'][:3])} | {_verdict(r)} | {_sec(r)} |")
        elif suite == "vision":
            lines += ["| ข้อ | ภาพที่ส่งเข้าระบบ | ผลวิเคราะห์ภาพ | คำตอบของบอท | ผลประเมิน | เวลาตอบ (วิ) |", "| :--- | :--- | :--- | :--- | :--- | :--- |"]
            for r in rows:
                v = (r.get("vision") or {}).get("summary", "")
                lines.append(f"| {r['id']} | {r['image']} | {_cell(v, 120)} | {_cell(r['reply'])} | {_verdict(r)} | {_sec(r)} |")
        else:
            lines += ["| ข้อ | กรณีทดสอบ | ข้อความ | ชั้นที่ดักจับ | คำตอบของบอท | ผลประเมิน | เวลาตอบ (วิ) |", "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"]
            for r in rows:
                layer = ", ".join(r["categories"]) if r["blocked"] else "System Prompt / RAG"
                lines.append(f"| {r['id']} | {r['case']} | {_cell(r['question'])} | {layer} | {_cell(r['reply'])} | {_verdict(r)} | {_sec(r)} |")
        fails = [r for r in rows if r["note"]]
        if fails:
            lines += ["", "หมายเหตุ:"] + [f"- {r['id']}: {r['note']}" for r in fails]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--only", choices=["rag", "vision", "safety"])
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    try:
        health = httpx.get(f"{args.base_url}/health", timeout=10).json()
    except httpx.HTTPError as exc:
        print(f"เชื่อมต่อ backend ที่ {args.base_url} ไม่ได้: {exc}")
        return 2
    if health.get("guard") == "down":
        print("⚠ Guard Service ไม่ทำงาน — ทุกข้อจะได้ HTTP 503 (เปิด guard ก่อน)")

    cases = json.loads((EVAL_DIR / "test_cases.json").read_text(encoding="utf-8"))
    suites = [args.only] if args.only else ["rag", "vision", "safety"]
    results = []
    for rnd in range(args.repeat):
        for suite in suites:
            for case in cases[suite]:
                r = run_case(args.base_url, suite, case)
                r["round"] = rnd + 1
                results.append(r)
                mark = {True: "✅", False: "❌", None: "⏭"}[r["passed"]]
                print(f"{mark} [{r['id']}] {_sec(r)}s  {_cell(r['reply'], 90)}  {r['note']}")

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    write_markdown(results, RESULTS_DIR / f"{stamp}.md", args.base_url, health)
    (RESULTS_DIR / f"{stamp}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(RESULTS_DIR / f"{stamp}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["round", "suite", "id", "question", "image", "reply", "blocked", "categories", "sources", "passed", "seconds", "note"])
        for r in results:
            w.writerow([r["round"], r["suite"], r["id"], r.get("question", ""), r.get("image", ""), r["reply"], r["blocked"],
                        " ".join(r["categories"]), " ".join(r["sources"]), _verdict(r), r["seconds"], r["note"]])

    ran = [r for r in results if r["passed"] is not None]
    print(f"\nผ่าน {sum(r['passed'] for r in ran)}/{len(ran)} ข้อ → eval/results/{stamp}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
