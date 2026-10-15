"""ชุดทดสอบ backend — จำลอง LLM และ Guard ด้วย httpx.MockTransport และใช้ embedder ปลอม
จึงรันได้โดยไม่ต้องมี API key / อินเทอร์เน็ต / โหลดโมเดล"""

import hashlib
import json
import math
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

KB_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "Rebooter.md"


class FakeEmbedder:
    """embedding จาก character bigram (ภาษาไทยไม่มีเว้นวรรค จึงใช้ bigram แทนคำ)"""

    dim = 512

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        t = "".join(text.lower().split())
        for i in range(len(t) - 1):
            h = int(hashlib.md5(t[i : i + 2].encode()).hexdigest(), 16) % self.dim
            v[h] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class FakeUpstream:
    """จำลอง LLM (/chat/completions) และ Guard (/check)"""

    def __init__(self):
        self.llm_calls: list[dict] = []
        self.guard_calls: list[dict] = []
        self.llm_reply = "ติดตั้ง RAM ราคา 200 บาทครับ"
        self.vision_reply = json.dumps({
            "is_it_related": True, "device": "PC", "visible_text": "Your PC ran into a problem STOP CODE: CRITICAL_PROCESS_DIED",
            "symptoms": ["จอฟ้า (BSOD)"], "summary": "หน้าจอฟ้าแสดงข้อผิดพลาดของ Windows", "image_quality": "ok",
        })
        self.guard_unsafe: dict[str, tuple[str, list[str]]] = {}  # direction -> (msg, cats)
        self.guard_status = 200
        self.llm_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/check"):
            self.guard_calls.append(body)
            if self.guard_status != 200:
                return httpx.Response(self.guard_status, json={"detail": "guard_unavailable"})
            d = body["direction"]
            if d in self.guard_unsafe:
                msg, cats = self.guard_unsafe[d]
                return httpx.Response(200, json={"msg": msg, "result": "unsafe", "direction": d, "categories": cats})
            return httpx.Response(200, json={"msg": body["message"], "result": "safe", "direction": d, "categories": []})
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path.endswith("/chat/completions"):
            self.llm_calls.append(body)
            if self.llm_status != 200:
                return httpx.Response(self.llm_status, json={"error": {"message": "x"}})
            is_vision = any(isinstance(m["content"], list) for m in body["messages"])
            content = self.vision_reply if is_vision else self.llm_reply
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        return httpx.Response(404)


@pytest.fixture
def upstream():
    return FakeUpstream()


@pytest.fixture
def client(upstream):
    s = Settings(
        _env_file=None,
        llm_api_key="test",
        llm_base_url="http://llm.test/v1",
        guard_url="http://guard.test",
        knowledge_path=str(KB_PATH),
    )
    app = create_app(settings=s, embedder=FakeEmbedder(), persist=False)
    with TestClient(app) as tc:
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
        yield tc
