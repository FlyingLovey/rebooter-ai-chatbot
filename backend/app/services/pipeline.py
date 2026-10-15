"""การไหลของข้อมูล 1 ข้อความ (Data Flow)

  1. Guard ตรวจข้อความลูกค้า (input)        ── unsafe → ตอบข้อความปฏิเสธทันที ไม่เรียก LLM
  2. ถ้ามีภาพ → Vision สรุปสิ่งที่เห็นในภาพ
  3. RAG ค้นคลังความรู้ด้วย (ข้อความ + ผลวิเคราะห์ภาพ)
  4. LLM ตอบโดยใช้ system prompt + context จากคลังความรู้ + ประวัติแชท
  5. Guard ตรวจคำตอบบอท (output)            ── unsafe → แทนคำตอบด้วยข้อความปฏิเสธ
  6. บันทึกประวัติ แล้วส่งคำตอบ + แหล่งข้อมูล + เวลาแต่ละขั้นกลับไปที่ UI
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..config import Settings
from . import guard_client
from .llm import chat_completion
from .rag import KnowledgeBase, format_context
from .sessions import SessionStore
from .vision import VisionResult, analyze_image

log = logging.getLogger("aura.pipeline")

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system.txt"


@dataclass
class ChatResult:
    reply: str
    blocked: bool = False
    blocked_by: str | None = None  # guard_input | guard_output
    categories: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)  # การ์ดบริการที่ตรงกับคำตอบ (แสดงใน UI)
    vision: dict | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)


class _Timer:
    def __init__(self):
        self.t0 = time.perf_counter()
        self.marks: dict[str, int] = {}

    def lap(self, name: str, start: float) -> None:
        self.marks[name] = round((time.perf_counter() - start) * 1000)

    def total(self) -> dict[str, int]:
        self.marks["total"] = round((time.perf_counter() - self.t0) * 1000)
        return self.marks


def service_cards(chunks, reply: str, limit: int = 2) -> list[dict]:
    """สร้างการ์ดบริการจาก chunk บริการ (S01–S20) ที่ราคาปรากฏอยู่ในคำตอบจริง
    การ์ดจึงแสดงเฉพาะบริการที่บอทพูดถึง และราคาบนการ์ดมาจากคลังความรู้ตรงๆ ไม่ได้มาจาก LLM"""
    cards = []
    for c in chunks:
        if not re.fullmatch(r"S\d{2}", c.id):
            continue
        lines = [ln.strip() for ln in c.text.splitlines()[1:] if ln.strip().startswith("- ")]
        price_line = next((ln for ln in lines if re.match(r"- (ราคา|ค่าเดินทาง)", ln)), "")
        price = re.sub(r"^- (ราคา|ค่าเดินทาง):\s*", "", price_line)
        nums = [n for n in re.findall(r"\d[\d,]*", price) if len(n.replace(",", "")) >= 3]
        mentioned = any(n in reply or n.replace(",", "") in reply for n in nums) or ("ฟรี" in price and "ฟรี" in reply)
        if not price or not mentioned:
            continue
        price = price.replace("**", "")
        # ราคาหลัก = ถึงคำว่า "บาท" ตัวสุดท้ายก่อนวงเล็บ ส่วนที่เหลือย้ายไปเป็นหมายเหตุ
        head = price.split("(")[0]
        m = re.match(r"^(.*บาท(?:/[^\s(]+)?)", head)
        main = m.group(1).strip() if m else head.strip()
        rest = price[len(main):].strip(" ()")
        if price_line.startswith("- ค่าเดินทาง"):
            main = "ค่าเดินทาง " + main
        notes = ([rest] if rest else []) + [ln[2:] for ln in lines if ln != price_line and "อะไหล่" not in ln]
        cards.append({
            "id": c.id,
            "title": re.sub(r"^S\d{2}\s*", "", c.title),
            "price": main,
            "parts_excluded": "ไม่รวมค่า" in c.text and "อะไหล่" in c.text,
            "notes": [n.replace("**", "") for n in notes][:2],
        })
        if len(cards) >= limit:
            break
    return cards


def build_messages(
    s: Settings, context: str, vision: VisionResult | None, history: list[dict], user_text: str
) -> list[dict]:
    system = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").replace("{shop_phone}", s.shop_phone)
    knowledge = f"## ข้อมูลจากคลังความรู้ของร้าน (ใช้ตอบได้เฉพาะข้อมูลนี้)\n{context or '(ไม่พบข้อมูลที่เกี่ยวข้อง)'}"
    msgs = [{"role": "system", "content": system}, {"role": "system", "content": knowledge}]
    if vision:
        msgs.append({"role": "system", "content": vision.as_context()})
    msgs.extend(history)
    msgs.append({"role": "user", "content": user_text or "ช่วยดูภาพที่ส่งไปให้หน่อยครับ"})
    return msgs


async def run_chat(
    *,
    client: httpx.AsyncClient,
    s: Settings,
    kb: KnowledgeBase,
    sessions: SessionStore,
    session_id: str,
    message: str,
    image: tuple[bytes, str] | None = None,
) -> ChatResult:
    timer = _Timer()
    text = message.strip()

    # 1) Guard: ข้อความลูกค้า
    if text:
        t = time.perf_counter()
        verdict = await guard_client.check(client, s, text, "input")
        timer.lap("guard_input", t)
        if not verdict.safe:
            log.info("input blocked %s", verdict.categories)
            return ChatResult(
                reply=verdict.msg, blocked=True, blocked_by="guard_input",
                categories=verdict.categories, timings_ms=timer.total(),
            )

    # 2) Vision
    vision: VisionResult | None = None
    if image:
        t = time.perf_counter()
        vision = await analyze_image(client, s, image[0], image[1], text)
        timer.lap("vision", t)

    # 3) RAG
    history = sessions.history(session_id)
    prev_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    query = " ".join(filter(None, [prev_user if len(text) < 25 else "", text, vision.as_query() if vision else ""]))
    t = time.perf_counter()
    chunks = await asyncio.to_thread(kb.search, query or "บริการของร้าน", s.rag_top_k)
    timer.lap("retrieval", t)

    # 4) LLM
    t = time.perf_counter()
    messages = build_messages(s, format_context(chunks), vision, history, text)
    reply = await chat_completion(client, s, messages)
    timer.lap("llm", t)
    if not reply:
        reply = f"ขออภัยครับ ระบบไม่สามารถตอบได้ในขณะนี้ กรุณาลองใหม่ หรือติดต่อร้านที่ {s.shop_phone}"

    # 5) Guard: คำตอบบอท
    t = time.perf_counter()
    verdict = await guard_client.check(client, s, reply, "output")
    timer.lap("guard_output", t)

    result = ChatResult(
        reply=reply if verdict.safe else verdict.msg,
        blocked=not verdict.safe,
        blocked_by=None if verdict.safe else "guard_output",
        categories=verdict.categories,
        sources=[{"id": c.id, "title": c.title, "score": c.score} for c in chunks],
        services=service_cards(chunks, reply) if verdict.safe else [],
        vision=vision.to_dict() if vision else None,
    )
    if not verdict.safe:
        log.warning("output blocked %s — original reply: %r", verdict.categories, reply[:200])

    # 6) ประวัติแชท (เก็บเฉพาะคำตอบที่ผ่าน Guard)
    user_turn = text if not image else f"{text} [แนบภาพ: {vision.summary if vision else ''}]".strip()
    sessions.append(session_id, user_turn, result.reply)
    result.timings_ms = timer.total()
    return result
