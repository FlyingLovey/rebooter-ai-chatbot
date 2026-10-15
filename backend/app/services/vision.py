"""วิเคราะห์ภาพที่ลูกค้าส่ง (หน้าจอ Error, จอแตก, อุปกรณ์เสีย) ให้เป็นข้อความสรุปอาการ

ผลลัพธ์ถูกนำไปใช้ 2 ทาง: (1) เป็นคำค้นของ RAG (2) แนบเป็นบริบทให้ LLM ตอบลูกค้า
ขั้นตอนนี้ไม่ตอบลูกค้าเอง และไม่ประเมินราคา — ราคาต้องมาจากคลังความรู้เท่านั้น
"""

import base64
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from ..config import Settings
from .llm import chat_completion

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "vision.txt"


@dataclass
class VisionResult:
    is_it_related: bool = True
    device: str = ""
    visible_text: str = ""
    symptoms: list[str] = field(default_factory=list)
    summary: str = ""
    image_quality: str = "ok"  # ok | unclear

    def as_query(self) -> str:
        return " ".join(filter(None, [self.summary, self.visible_text, " ".join(self.symptoms), self.device]))

    def as_context(self) -> str:
        return (
            "ผลวิเคราะห์ภาพที่ลูกค้าส่งมา:\n"
            f"- เกี่ยวกับอุปกรณ์ไอที: {'ใช่' if self.is_it_related else 'ไม่ใช่'}\n"
            f"- อุปกรณ์: {self.device or '-'}\n"
            f"- ข้อความที่อ่านได้ในภาพ: {self.visible_text or '-'}\n"
            f"- อาการที่เห็น: {', '.join(self.symptoms) or '-'}\n"
            f"- คุณภาพภาพ: {'ไม่ชัด' if self.image_quality == 'unclear' else 'ชัดเจน'}\n"
            f"- สรุป: {self.summary or '-'}"
        )

    def to_dict(self) -> dict:
        return asdict(self)


def parse_json_object(raw: str) -> dict | None:
    text = raw.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    for candidate in (text, (re.search(r"\{.*\}", text, re.DOTALL) or [None])[0]):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _to_result(obj: dict) -> VisionResult:
    symptoms = obj.get("symptoms") or []
    if isinstance(symptoms, str):
        symptoms = [symptoms]
    return VisionResult(
        is_it_related=bool(obj.get("is_it_related", True)),
        device=str(obj.get("device") or ""),
        visible_text=str(obj.get("visible_text") or ""),
        symptoms=[str(x) for x in symptoms][:6],
        summary=str(obj.get("summary") or ""),
        image_quality="unclear" if str(obj.get("image_quality", "ok")).lower() == "unclear" else "ok",
    )


async def analyze_image(
    client: httpx.AsyncClient, s: Settings, image_bytes: bytes, mime: str, user_text: str
) -> VisionResult:
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    messages = [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text or "ช่วยดูภาพนี้ให้หน่อย"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    raw = await chat_completion(client, s, messages, model=s.vision_model, temperature=0, max_tokens=400)
    obj = parse_json_object(raw)
    if obj is None:  # ลองอีกครั้ง ย้ำให้ตอบ JSON
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "กรุณาตอบใหม่เป็น JSON object เดียวตามรูปแบบที่กำหนดเท่านั้น"},
        ]
        raw = await chat_completion(client, s, messages, model=s.vision_model, temperature=0, max_tokens=400)
        obj = parse_json_object(raw)
    if obj is None:
        return VisionResult(summary=raw[:300])
    return _to_result(obj)
