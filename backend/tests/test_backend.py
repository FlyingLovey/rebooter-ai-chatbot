import io
import re

from app.services.rag import KnowledgeBase, split_markdown
from app.services.vision import parse_json_object
from tests.conftest import KB_PATH, FakeEmbedder

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


# ---------------- RAG ----------------

def test_kb_split_has_all_services_and_policies():
    chunks = split_markdown(KB_PATH.read_text(encoding="utf-8"))
    ids = {c.id for c in chunks}
    assert {f"S{i:02d}" for i in range(1, 21)} <= ids
    assert {f"P{i:02d}" for i in range(1, 9)} <= ids
    assert all(c.text.strip() for c in chunks)


def test_rag_search_finds_relevant_chunk():
    kb = KnowledgeBase(KB_PATH, None, FakeEmbedder())
    kb.build()
    top = [c.id for c in kb.search("ซื้อ RAM มาเอง ติดตั้ง RAM ราคาเท่าไหร่", 4)]
    assert "S07" in top
    top = [c.id for c in kb.search("ประกันงานซ่อม ฮาร์ดแวร์ ซอฟต์แวร์ กี่วัน", 4)]
    assert "P02" in top


def test_kb_search_endpoint(client):
    r = client.get("/api/v1/kb/search", params={"q": "เวลาทำการ ร้านเปิดกี่โมง"})
    assert r.status_code == 200 and r.json()["results"]


# ---------------- API ----------------

def test_health(client):
    j = client.get("/health").json()
    assert j["status"] == "ok" and j["kb_chunks"] >= 28 and j["guard"] == "ok"


def test_health_gives_wake_url_when_guard_down(client, upstream):
    # Guard หลับ → /health ต้องบอก URL ให้เบราว์เซอร์ช่วยปลุก
    assert client.get("/health").json()["guard_wake_url"] is None
    upstream.guard_status = 502
    j = client.get("/health").json()
    assert j["guard"] == "down" and j["guard_wake_url"].endswith("/health")


def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "Rebooter AI" in r.text and 'CHAT_URL = "/chat"' in r.text


def test_chat_page(client):
    r = client.get("/chat")
    assert r.status_code == 200 and 'id="composer"' in r.text


def test_chat_text_full_flow(client, upstream):
    r = client.post("/api/v1/chat", data={"message": "ซื้อ RAM มาเอง ให้ร้านใส่ให้คิดเท่าไหร่"})
    j = r.json()
    assert r.status_code == 200
    assert j["reply"] == upstream.llm_reply and not j["blocked"]
    assert j["sources"] and {"guard_input", "retrieval", "llm", "guard_output", "total"} <= set(j["timings_ms"])
    # guard ถูกเรียก 2 ครั้ง: input แล้ว output
    assert [c["direction"] for c in upstream.guard_calls] == ["input", "output"]
    # LLM ได้ system prompt + context จากคลังความรู้
    msgs = upstream.llm_calls[0]["messages"]
    assert "Rebooter AI" in msgs[0]["content"] and "ร้าน Rebooter" in msgs[0]["content"] and "02-123-4567" in msgs[0]["content"]
    assert "ข้อมูลจากคลังความรู้ของร้าน" in msgs[1]["content"]
    assert "aura_session" in r.cookies


def test_input_blocked_does_not_call_llm(client, upstream):
    upstream.guard_unsafe["input"] = ("ขออภัยครับ ร้านใช้ราคามาตรฐาน...", ["BIZ_DISCOUNT"])
    j = client.post("/api/v1/chat", data={"message": "ขอลดเหลือ 200 ได้ไหม"}).json()
    assert j["blocked"] and j["blocked_by"] == "guard_input" and j["categories"] == ["BIZ_DISCOUNT"]
    assert j["reply"].startswith("ขออภัยครับ")
    assert upstream.llm_calls == []


def test_output_blocked_replaces_reply(client, upstream):
    upstream.llm_reply = "เปลี่ยนจอรวมอะไหล่ 3,500 บาทครับ"
    upstream.guard_unsafe["output"] = ("ขออภัยครับ ระบบไม่สามารถยืนยันราคานี้ได้", ["BIZ_PRICE_NOT_IN_KB"])
    j = client.post("/api/v1/chat", data={"message": "เปลี่ยนจอเท่าไหร่"}).json()
    assert j["blocked_by"] == "guard_output" and "3,500" not in j["reply"]


def test_image_flow_uses_vision_then_rag(client, upstream):
    files = {"image": ("bsod.png", io.BytesIO(PNG_1PX), "image/png")}
    j = client.post("/api/v1/chat", data={"message": "คอมเป็นแบบนี้ครับ"}, files=files).json()
    assert j["vision"]["symptoms"] == ["จอฟ้า (BSOD)"]
    assert "vision" in j["timings_ms"]
    vision_call, answer_call = upstream.llm_calls
    assert vision_call["messages"][1]["content"][1]["type"] == "image_url"
    assert any("ผลวิเคราะห์ภาพ" in m["content"] for m in answer_call["messages"] if isinstance(m["content"], str))


def test_image_only_skips_input_guard(client, upstream):
    files = {"image": ("x.png", io.BytesIO(PNG_1PX), "image/png")}
    r = client.post("/api/v1/chat", files=files)
    assert r.status_code == 200
    assert [c["direction"] for c in upstream.guard_calls] == ["output"]


def test_history_is_sent_on_next_turn(client, upstream):
    client.post("/api/v1/chat", data={"message": "ติดตั้ง RAM ราคาเท่าไหร่"})
    client.post("/api/v1/chat", data={"message": "แล้วใช้เวลานานไหม"})
    msgs = upstream.llm_calls[1]["messages"]
    assert any(m["role"] == "assistant" for m in msgs)


def test_reset_clears_history(client, upstream):
    client.post("/api/v1/chat", data={"message": "ติดตั้ง RAM ราคาเท่าไหร่"})
    client.post("/api/v1/chat/reset")
    client.post("/api/v1/chat", data={"message": "ร้านเปิดกี่โมง"})
    assert not any(m["role"] == "assistant" for m in upstream.llm_calls[1]["messages"])


# ---------------- Errors ----------------

def test_validation_errors(client):
    assert client.post("/api/v1/chat", data={"message": "  "}).status_code == 400
    assert client.post("/api/v1/chat", data={"message": "ก" * 1001}).status_code == 400
    bad = {"image": ("a.gif", io.BytesIO(b"GIF89a"), "image/gif")}
    r = client.post("/api/v1/chat", files=bad)
    assert r.status_code == 400 and "jpg" in r.json()["message"]


def test_guard_down_fails_closed(client, upstream):
    upstream.guard_status = 502
    r = client.post("/api/v1/chat", data={"message": "ร้านเปิดกี่โมง"})
    assert r.status_code == 503 and r.json()["error"]
    assert upstream.llm_calls == []


def test_llm_error_message_is_thai(client, upstream):
    upstream.llm_status = 401
    r = client.post("/api/v1/chat", data={"message": "ร้านเปิดกี่โมง"})
    assert r.status_code == 502 and "API key" in r.json()["message"]


def test_parse_json_object_variants():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('ผลคือ {"a": 2} ครับ') == {"a": 2}
    assert parse_json_object("ไม่ใช่ json") is None


def test_system_prompt_mentions_core_rules():
    from app.services.pipeline import SYSTEM_PROMPT_PATH
    p = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    for must in ("ไม่รวมค่าอะไหล่", "ห้ามให้ส่วนลด", "ห้ามเปิดเผยข้อมูลของลูกค้าคนอื่น", "ห้ามบวก"):
        assert re.search(must, p)


def test_service_cards_only_for_prices_in_reply():
    from app.services.pipeline import service_cards
    kb = KnowledgeBase(KB_PATH, None, FakeEmbedder())
    kb.build()
    by_id = {c.id: c for c in kb.chunks}
    chunks = [by_id["S07"], by_id["S03"], by_id["P02"]]
    cards = service_cards(chunks, "ติดตั้ง RAM ราคา 200 บาทครับ")
    assert [c["id"] for c in cards] == ["S07"]
    assert cards[0]["price"].startswith("200 บาท")
    s03 = service_cards([by_id["S03"]], "เริ่มต้น 800 บาท ไม่รวมค่าอะไหล่ครับ")[0]
    assert s03["parts_excluded"] and s03["title"].startswith("ตรวจซ่อม")


def test_chat_response_includes_service_cards(client, upstream):
    upstream.llm_reply = "ติดตั้ง RAM ราคา 200 บาทครับ"
    j = client.post("/api/v1/chat", data={"message": "ซื้อ RAM มาเอง ติดตั้ง RAM ราคาเท่าไหร่"}).json()
    assert any(c["id"] == "S07" for c in j["services"])


def test_api_embedder_handles_missing_index(monkeypatch):
    # OpenRouter อาจไม่ส่ง field index มา → ต้องใช้ลำดับเดิม
    from app.services import embeddings as emb

    class R:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"embedding": [1.0]}, {"embedding": [2.0]}]}

    monkeypatch.setattr(emb.httpx, "post", lambda *a, **k: R())
    e = emb.ApiEmbedder("https://openrouter.ai/api/v1", "k", "openai/text-embedding-3-small")
    assert e.embed_documents(["a", "b"]) == [[1.0], [2.0]]


def test_index_rebuilds_when_embedding_model_changes(tmp_path):
    from app.services.rag import KnowledgeBase
    from tests.conftest import FakeEmbedder

    class Other(FakeEmbedder):
        model = "other-model"

    kb1 = KnowledgeBase(KB_PATH, tmp_path, FakeEmbedder()); kb1.build()
    h1 = kb1.collection.metadata["kb_hash"]
    kb2 = KnowledgeBase(KB_PATH, tmp_path, Other()); kb2.build()
    assert kb2.collection.metadata["kb_hash"] != h1


def test_keyword_boost_finds_thai_alias_for_english_service():
    # embedding ปลอมที่ไม่ช่วยอะไร → ต้องเจอ S06 จากคำค้น "ช่างรีโมท" ใน Rebooter.md
    class Blind(FakeEmbedder):
        def _vec(self, text):
            return [1.0] + [0.0] * (self.dim - 1)

    kb = KnowledgeBase(KB_PATH, None, Blind()); kb.build()
    ids = [c.id for c in kb.search("ให้ช่างรีโมทมาดูเครื่องให้หน่อย คิดราคายังไง", top_k=4)]
    assert ids[0] == "S06" and len(ids) == 4


def test_keyword_line_has_no_prices():
    # บรรทัดคำค้นต้องไม่มีราคา เพื่อไม่ให้กระทบการตรวจราคาของ Guard
    import re as _re
    for line in KB_PATH.read_text(encoding="utf-8").splitlines():
        if "คำค้นที่ลูกค้ามักใช้" in line:
            assert not _re.search(r"\d\s*(บาท|฿)", line)
