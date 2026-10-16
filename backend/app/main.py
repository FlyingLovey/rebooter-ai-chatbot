import asyncio
import time
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .routers.chat import router as chat_router
from .services.embeddings import Embedder, build_embedder
from .services.rag import KnowledgeBase
from .services.sessions import SessionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aura")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: Settings | None = None, embedder: Embedder | None = None, persist: bool = True) -> FastAPI:
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = s
        app.state.http = httpx.AsyncClient()
        app.state.sessions = SessionStore(s.history_turns)
        kb_path = s.resolve(s.knowledge_path)
        if embedder is None and s.embedding_provider == "local":
            log.info("กำลังโหลดโมเดล embedding %s (ครั้งแรกต้องดาวน์โหลด ~470 MB อาจใช้เวลาหลายนาที)...", s.embedding_model)
        app.state.kb = KnowledgeBase(
            kb_path,
            s.resolve(s.chroma_dir) if persist else None,
            embedder or build_embedder(s),
        )
        n = app.state.kb.build()
        log.info("พร้อมให้บริการ: คลังความรู้ %d chunks, model=%s, guard=%s",
                 n, s.chat_model, s.guard_url if s.guard_enabled else "ปิด")
        if not s.llm_api_key:
            log.warning("ยังไม่ได้ตั้งค่า LLM_API_KEY ใน .env")
        yield
        await app.state.http.aclose()

    app = FastAPI(title="Rebooter AI", version="1.0.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(chat_router)

    def wake_guard() -> None:
        """ปลุก Guard แบบไม่รอผล (บน Render แผนฟรี Guard จะหลับหลังว่าง 15 นาที ตื่นช้าราว 1 นาที)
        เรียกทุกครั้งที่มีคนเปิดหน้าเว็บ — จำกัดไม่เกิน 1 ครั้งต่อ 60 วินาที"""
        st = app.state
        if not getattr(st, "settings", None) or not st.settings.guard_enabled:
            return
        now = time.monotonic()
        if now - getattr(st, "last_wake", -1e9) < 60:
            return
        st.last_wake = now

        async def ping():
            try:
                await st.http.get(f"{st.settings.guard_url.rstrip('/')}/health", timeout=st.settings.guard_timeout_seconds)
            except Exception:  # noqa: BLE001 — ปลุกไม่สำเร็จไม่เป็นไร คำขอจริงจะลองใหม่เอง
                pass

        st.wake_task = asyncio.create_task(ping())

    @app.get("/", include_in_schema=False)
    async def home():
        # หน้าแรกของร้าน มีปุ่มแชทลอยที่เปิด /chat
        wake_guard()
        return FileResponse(STATIC_DIR / "home.html")

    @app.get("/chat", include_in_schema=False)
    async def chat_page():
        wake_guard()
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    async def health(request: Request):
        st = request.app.state
        guard = "disabled"
        if st.settings.guard_enabled:
            try:
                r = await st.http.get(f"{st.settings.guard_url.rstrip('/')}/health", timeout=3)
                guard = "ok" if r.status_code == 200 else "down"
            except httpx.HTTPError:
                guard = "down"
            if guard == "down":
                wake_guard()
        return {
            "status": "ok",
            "kb_chunks": len(st.kb.chunks),
            "guard": guard,
            "chat_model": st.settings.chat_model,
            "vision_model": st.settings.vision_model,
        }

    return app


app = create_app()
