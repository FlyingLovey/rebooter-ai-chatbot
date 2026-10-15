import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from ..services.guard_client import GuardUnavailable
from ..services.llm import LLMError
from ..services.pipeline import run_chat

log = logging.getLogger("aura.api")
router = APIRouter(prefix="/api/v1")

SESSION_COOKIE = "aura_session"
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": True, "message": message})


def _session(request: Request) -> tuple[str, bool]:
    return request.app.state.sessions.ensure(request.cookies.get(SESSION_COOKIE))


def _with_cookie(resp: JSONResponse, session_id: str, is_new: bool) -> JSONResponse:
    if is_new:
        resp.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="lax", max_age=60 * 60 * 6)
    return resp


@router.post("/chat")
async def post_chat(
    request: Request,
    message: str = Form(""),
    image: UploadFile | None = File(None),
):
    s = request.app.state.settings
    text = message.strip()
    has_image = image is not None and bool(image.filename)

    if not text and not has_image:
        return _error(400, "กรุณาพิมพ์ข้อความหรือแนบรูปภาพ")
    if len(text) > s.max_message_chars:
        return _error(400, f"ข้อความยาวเกินไป (ไม่เกิน {s.max_message_chars} ตัวอักษร)")

    img: tuple[bytes, str] | None = None
    if has_image:
        mime = (image.content_type or "").lower()
        if mime not in ALLOWED_MIME:
            return _error(400, "รองรับเฉพาะไฟล์ภาพ .jpg .png .webp เท่านั้น")
        data = await image.read()
        if not data:
            return _error(400, "ไฟล์ภาพว่างเปล่าหรืออ่านไม่ได้")
        if len(data) > s.max_image_mb * 1024 * 1024:
            return _error(400, f"ไฟล์ภาพใหญ่เกินไป (ไม่เกิน {s.max_image_mb:g} MB)")
        img = (data, mime)

    session_id, is_new = _session(request)
    try:
        result = await run_chat(
            client=request.app.state.http,
            s=s,
            kb=request.app.state.kb,
            sessions=request.app.state.sessions,
            session_id=session_id,
            message=text,
            image=img,
        )
    except GuardUnavailable:
        return _with_cookie(_error(503, "ระบบตรวจความปลอดภัยไม่พร้อมใช้งาน จึงยังตอบไม่ได้ กรุณาลองใหม่อีกครั้ง"), session_id, is_new)
    except LLMError as exc:
        return _with_cookie(_error(502, str(exc)), session_id, is_new)
    except Exception:
        log.exception("chat ล้มเหลว")
        return _with_cookie(_error(500, "เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่อีกครั้ง"), session_id, is_new)

    log.info(
        "chat session=%s blocked=%s cats=%s timings=%s",
        session_id[:8], result.blocked_by, result.categories, result.timings_ms,
    )
    return _with_cookie(JSONResponse(content=result.__dict__), session_id, is_new)


@router.post("/chat/reset")
async def post_reset(request: Request):
    session_id, is_new = _session(request)
    request.app.state.sessions.reset(session_id)
    return _with_cookie(JSONResponse(content={"ok": True}), session_id, is_new)


@router.get("/kb/search")
async def kb_search(request: Request, q: str, k: int = 4):
    """ใช้ทดสอบ/สาธิต RAG: ดูว่าคำถามนี้ค้นเจอ chunk ไหนบ้าง"""
    if not q.strip():
        return _error(400, "กรุณาระบุคำค้น q")
    chunks = request.app.state.kb.search(q, max(1, min(k, 10)))
    return {"query": q, "results": [{"id": c.id, "title": c.title, "score": c.score, "text": c.text} for c in chunks]}
