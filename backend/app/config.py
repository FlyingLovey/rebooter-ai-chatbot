"""ค่าตั้งค่าทั้งหมดของ backend อ่านจาก .env (ผ่าน pydantic-settings)"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# โฟลเดอร์ backend/ — path แบบ relative ใน .env จะอ้างอิงจากที่นี่ ไม่ขึ้นกับว่ารันจากโฟลเดอร์ไหน
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM (OpenAI-compatible: OpenRouter หรือ LM Studio) ---
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    chat_model: str = "openai/gpt-4o-mini"
    vision_model: str = "openai/gpt-4o-mini"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 700
    llm_timeout_seconds: float = 60.0

    # --- RAG ---
    knowledge_path: str = "../knowledge/Rebooter.md"
    chroma_dir: str = "chroma_db"
    rag_top_k: int = 4
    # local = sentence-transformers บนเครื่อง | api = เรียก {embedding_base_url}/embeddings แบบ OpenAI-compatible
    embedding_provider: str = "local"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_base_url: str = ""
    embedding_api_key: str = ""

    # --- Guard Service ---
    guard_enabled: bool = True
    guard_url: str = "http://127.0.0.1:8001"
    guard_timeout_seconds: float = 25.0

    # --- App ---
    max_message_chars: int = 1000
    max_image_mb: float = 5.0
    history_turns: int = 6
    shop_phone: str = "02-123-4567"

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
