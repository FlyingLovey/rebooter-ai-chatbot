import logging
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .blocklist import Blocklist
from .business_rules import BusinessRules, service_override
from .categories import BLOCKLIST_CODE, BLOCKLIST_MESSAGE, BUSINESS_MESSAGES, CATEGORIES
from .guard import GuardError, classify
from .settings import get_settings

log = logging.getLogger("nvd-guard")
settings = get_settings()
blocklist = Blocklist(settings.blocklist_path)
business_rules = BusinessRules(settings.knowledge_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient()
    yield
    await app.state.http.aclose()


app = FastAPI(title="NVD Guard", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


class CheckRequest(BaseModel):
    message: str = Field(min_length=1)
    direction: Literal["input", "output"]


class CheckResponse(BaseModel):
    msg: str
    result: Literal["safe", "unsafe"]
    direction: Literal["input", "output"]
    categories: list[str] = []


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/check", response_model=CheckResponse)
async def check(req: CheckRequest) -> CheckResponse:
    # คำต้องห้ามตรวจก่อน: ผลแน่นอนและไม่เสียค่าเรียกโมเดล
    if blocklist.matches(req.message):
        return CheckResponse(
            msg=BLOCKLIST_MESSAGE,
            result="unsafe",
            direction=req.direction,
            categories=[BLOCKLIST_CODE],
        )

    # กฎของร้าน: ส่วนลด / ข้อมูลลูกค้าอื่น / เปลี่ยนนโยบาย / prompt injection / ราคาที่ไม่มีในคลังความรู้
    # ตรวจก่อนเรียกโมเดล เพราะ Llama Guard ไม่ถือว่ากรณีเหล่านี้ไม่ปลอดภัย
    biz_code = business_rules.check(req.message, req.direction)
    if biz_code:
        log.info("business rule blocked: %s (%s)", biz_code, req.direction)
        return CheckResponse(
            msg=BUSINESS_MESSAGES[biz_code],
            result="unsafe",
            direction=req.direction,
            categories=[biz_code],
        )

    try:
        unsafe, codes = await classify(app.state.http, settings, req.message, req.direction)
    except GuardError as exc:
        # fail-closed: ตรวจไม่ได้ ต้องไม่ตอบว่า safe
        log.error("guard failed: %s", exc)
        raise HTTPException(status_code=502, detail="guard_unavailable") from exc

    if not unsafe:
        return CheckResponse(msg=req.message, result="safe", direction=req.direction)

    # Llama Guard ตีความงานบริการของร้าน (กู้ข้อมูล / ไวรัส) เป็นอาชญากรรม S2 → ยอมให้ผ่านเฉพาะเมื่อไม่มีเจตนาโจมตี
    if service_override(req.message, codes):
        log.info("llama guard %s overridden: service context (%s)", codes, req.direction)
        return CheckResponse(msg=req.message, result="safe", direction=req.direction)

    return CheckResponse(
        msg=CATEGORIES[codes[0]].message,
        result="unsafe",
        direction=req.direction,
        categories=codes,
    )
