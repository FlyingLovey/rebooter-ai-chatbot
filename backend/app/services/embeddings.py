"""ตัวแปลงข้อความเป็นเวกเตอร์ (embedding) สำหรับ RAG

ค่าเริ่มต้นใช้ intfloat/multilingual-e5-small ซึ่งรองรับภาษาไทย รันบนเครื่องได้ (CPU)
โมเดลตระกูล e5 ต้องเติม prefix "query: " / "passage: " ตามที่ผู้สร้างโมเดลกำหนด
"""

from typing import Protocol

import httpx

from ..config import Settings


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    def __init__(self, model_name: str):
        # import ตอนใช้งานจริง เพื่อไม่ให้ test/ส่วนอื่นต้องโหลด torch
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self._e5 = "e5" in model_name.lower()

    def _encode(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode([f"passage: {t}" if self._e5 else t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([f"query: {text}" if self._e5 else text])[0]


class ApiEmbedder:
    """เรียก POST {base_url}/embeddings แบบ OpenAI-compatible (เช่น LM Studio, OpenAI)"""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.url = base_url.rstrip("/") + "/embeddings"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.model = model

    def _call(self, texts: list[str]) -> list[list[float]]:
        resp = httpx.post(self.url, headers=self.headers, json={"model": self.model, "input": texts}, timeout=60)
        resp.raise_for_status()
        data = resp.json()["data"]
        # บางผู้ให้บริการไม่ส่ง index มา → ใช้ลำดับเดิม
        data = sorted(enumerate(data), key=lambda p: p[1].get("index", p[0]))
        return [d["embedding"] for _, d in data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._call(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._call([text])[0]


def build_embedder(s: Settings) -> Embedder:
    if s.embedding_provider == "api":
        if not s.embedding_base_url:
            raise ValueError("EMBEDDING_PROVIDER=api ต้องตั้งค่า EMBEDDING_BASE_URL")
        return ApiEmbedder(s.embedding_base_url, s.embedding_api_key, s.embedding_model)
    return LocalEmbedder(s.embedding_model)
