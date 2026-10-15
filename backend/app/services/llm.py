"""เรียก LLM แบบ OpenAI-compatible Chat Completions (OpenRouter หรือ LM Studio)"""

import logging
from typing import Any

import httpx

from ..config import Settings

log = logging.getLogger("aura.llm")


class LLMError(Exception):
    """ข้อความใน exception เป็นภาษาไทยที่ปลอดภัยพอจะแสดงให้ผู้ใช้เห็นได้"""


def _friendly(status: int) -> str:
    return {
        401: "เชื่อมต่อ AI ไม่สำเร็จ: API key ไม่ถูกต้อง",
        402: "เครดิตของผู้ให้บริการ AI ไม่พอ",
        404: "ไม่พบโมเดล AI ที่ตั้งค่าไว้",
        429: "มีการใช้งานถี่เกินไป กรุณาลองใหม่อีกครั้งในอีกสักครู่",
        503: "ผู้ให้บริการ AI ไม่พร้อมให้บริการตอนนี้",
    }.get(status, f"ผู้ให้บริการ AI ตอบกลับด้วยข้อผิดพลาด ({status})")


async def chat_completion(
    client: httpx.AsyncClient,
    s: Settings,
    messages: list[dict[str, Any]],
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    payload = {
        "model": model or s.chat_model,
        "messages": messages,
        "temperature": s.llm_temperature if temperature is None else temperature,
        "max_tokens": max_tokens or s.llm_max_tokens,
    }
    try:
        resp = await client.post(
            f"{s.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {s.llm_api_key}"},
            json=payload,
            timeout=s.llm_timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        raise LLMError("AI ตอบสนองช้าเกินไป (timeout) กรุณาลองใหม่อีกครั้ง") from exc
    except httpx.HTTPError as exc:
        raise LLMError("เชื่อมต่อผู้ให้บริการ AI ไม่ได้ กรุณาตรวจสอบอินเทอร์เน็ตหรือ LLM_BASE_URL") from exc

    if resp.status_code != 200:
        log.error("LLM error %s: %s", resp.status_code, resp.text[:300])
        raise LLMError(_friendly(resp.status_code))
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise LLMError("รูปแบบคำตอบจาก AI ไม่ถูกต้อง") from exc
    return (content or "").strip()
