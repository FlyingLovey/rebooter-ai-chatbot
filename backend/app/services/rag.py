"""RAG: ตัดคลังความรู้เป็น chunk → ทำ embedding → เก็บใน ChromaDB → ค้นคืนตามคำถาม

- 1 chunk = 1 หัวข้อระดับ "## " ในไฟล์คลังความรู้ (บริการ 1 รายการ / นโยบาย 1 ข้อ / ตาราง 1 ตาราง)
- index ถูกสร้างใหม่อัตโนมัติเมื่อเนื้อหาไฟล์คลังความรู้เปลี่ยน (เทียบ hash) ไม่ต้องสั่งเอง
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import chromadb

from .embeddings import Embedder

log = logging.getLogger("aura.rag")

COLLECTION = "aura_kb"

# id อ่านง่ายสำหรับหัวข้อที่ไม่มีรหัส S/P (แสดงเป็น "แหล่งข้อมูล" ใน UI และผลทดสอบ)
NAMED_SECTIONS = {
    "ข้อมูลร้าน": "INFO",
    "รายการบริการและราคา": "PRICES",
    "ไม่ให้บริการ": "OUT_OF_SCOPE",
    "การจับคู่ภาพ": "ERROR_MAP",
    "คำถามที่พบบ่อย": "FAQ",
}


@dataclass
class Chunk:
    id: str
    title: str
    text: str
    score: float = 0.0  # cosine similarity (ยิ่งมากยิ่งใกล้)


def split_markdown(md: str) -> list[Chunk]:
    """ตัดไฟล์ markdown ตามหัวข้อ "## " — ส่วนหัวไฟล์ก่อนหัวข้อแรกและเส้นคั่น --- ไม่นับเป็น chunk"""
    chunks: list[Chunk] = []
    parts = re.split(r"(?m)^## ", md)
    for part in parts[1:]:
        title, _, body = part.partition("\n")
        body = re.sub(r"(?m)^---\s*$", "", body).strip()
        if not body:
            continue
        title = title.strip()
        code = re.match(r"([SP]\d{2})\b", title) or re.search(r"\b([SP]\d{2})\b", title)
        named = next((v for k, v in NAMED_SECTIONS.items() if k in title), None)
        cid = code.group(1) if code else named or hashlib.md5(title.encode()).hexdigest()[:8]
        chunks.append(Chunk(id=cid, title=title, text=f"{title}\n{body}"))
    # กัน id ซ้ำ
    seen: dict[str, int] = {}
    for c in chunks:
        if c.id in seen:
            seen[c.id] += 1
            c.id = f"{c.id}-{seen[c.id]}"
        else:
            seen[c.id] = 0
    return chunks


class KnowledgeBase:
    def __init__(self, kb_path: Path, chroma_dir: Path | None, embedder: Embedder):
        self.kb_path = kb_path
        self.embedder = embedder
        self.client = (
            chromadb.PersistentClient(path=str(chroma_dir)) if chroma_dir else chromadb.EphemeralClient()
        )
        self.collection = None
        self.chunks: list[Chunk] = []

    def build(self) -> int:
        """สร้าง/โหลด index คืนจำนวน chunk"""
        md = self.kb_path.read_text(encoding="utf-8-sig")
        # ผูก index กับทั้งเนื้อหาไฟล์และโมเดล embedding: เปลี่ยนโมเดล (เช่น local → api) แล้วต้องสร้างใหม่
        emb_id = f"{type(self.embedder).__name__}:{getattr(self.embedder, 'model_name', None) or getattr(self.embedder, 'model', '')}"
        kb_hash = hashlib.sha256((md + "\n" + emb_id).encode("utf-8")).hexdigest()
        self.chunks = split_markdown(md)

        existing = None
        try:
            existing = self.client.get_collection(COLLECTION)
        except Exception:
            existing = None

        if existing is not None and (existing.metadata or {}).get("kb_hash") == kb_hash and existing.count() == len(self.chunks):
            self.collection = existing
            log.info("ใช้ index เดิม (%d chunks)", len(self.chunks))
            return len(self.chunks)

        if existing is not None:
            self.client.delete_collection(COLLECTION)
        self.collection = self.client.create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine", "kb_hash": kb_hash}
        )
        self.collection.add(
            ids=[c.id for c in self.chunks],
            documents=[c.text for c in self.chunks],
            embeddings=self.embedder.embed_documents([c.text for c in self.chunks]),
            metadatas=[{"title": c.title} for c in self.chunks],
        )
        log.info("สร้าง index ใหม่ %d chunks จาก %s", len(self.chunks), self.kb_path.name)
        return len(self.chunks)

    def search(self, query: str, top_k: int = 4) -> list[Chunk]:
        if self.collection is None:
            raise RuntimeError("ยังไม่ได้สร้าง index (เรียก build() ก่อน)")
        res = self.collection.query(
            query_embeddings=[self.embedder.embed_query(query)],
            n_results=min(top_k, len(self.chunks)),
        )
        out = []
        for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
            out.append(Chunk(id=cid, title=meta.get("title", ""), text=doc, score=round(1 - dist, 4)))
        return out


def format_context(chunks: list[Chunk]) -> str:
    return "\n\n".join(f"[{c.id}] {c.text}" for c in chunks)
