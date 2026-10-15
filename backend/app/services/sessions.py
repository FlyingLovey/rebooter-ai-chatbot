"""เก็บประวัติบทสนทนาต่อ session (in-memory)

ข้อจำกัด: รีสตาร์ต server แล้วหายหมด และใช้ได้กับ process เดียว — พอสำหรับต้นแบบ
ถ้าจะใช้จริงหลาย instance ควรย้ายไป Redis/ฐานข้อมูล โดยใช้ session_id เดิมเป็น key
"""

import time
import uuid
from collections import OrderedDict

MAX_SESSIONS = 500
SESSION_TTL_SECONDS = 60 * 60 * 6


class SessionStore:
    def __init__(self, max_turns: int):
        self.max_turns = max_turns
        self._data: OrderedDict[str, tuple[float, list[dict[str, str]]]] = OrderedDict()

    def ensure(self, session_id: str | None) -> tuple[str, bool]:
        self._evict()
        if session_id and session_id in self._data:
            return session_id, False
        new_id = uuid.uuid4().hex
        self._data[new_id] = (time.time(), [])
        return new_id, True

    def history(self, session_id: str) -> list[dict[str, str]]:
        return list(self._data.get(session_id, (0, []))[1])

    def append(self, session_id: str, user_text: str, bot_text: str) -> None:
        hist = self.history(session_id) + [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": bot_text},
        ]
        self._data[session_id] = (time.time(), hist[-self.max_turns * 2 :])
        self._data.move_to_end(session_id)

    def reset(self, session_id: str) -> None:
        self._data[session_id] = (time.time(), [])

    def _evict(self) -> None:
        now = time.time()
        for sid in [k for k, (ts, _) in self._data.items() if now - ts > SESSION_TTL_SECONDS]:
            del self._data[sid]
        while len(self._data) > MAX_SESSIONS:
            self._data.popitem(last=False)
