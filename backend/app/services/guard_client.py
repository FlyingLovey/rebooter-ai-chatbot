"""เรียก Guard Service (POST /check) — ใช้ 2 ครั้งต่อ 1 ข้อความ: ตรวจข้อความลูกค้า (input) และคำตอบบอท (output)

Fail-closed: ถ้าเรียก Guard ไม่ได้ จะถือว่าตรวจไม่ผ่าน (ไม่ปล่อยข้อความที่ยังไม่ได้ตรวจออกไป)
"""

import logging
from dataclasses import dataclass, field

import httpx

from ..config import Settings

log = logging.getLogger("aura.guard")


class GuardUnavailable(Exception):
    pass


@dataclass
class GuardVerdict:
    safe: bool
    msg: str
    categories: list[str] = field(default_factory=list)
    skipped: bool = False


async def check(client: httpx.AsyncClient, s: Settings, message: str, direction: str) -> GuardVerdict:
    if not s.guard_enabled:
        return GuardVerdict(safe=True, msg=message, skipped=True)
    try:
        resp = await client.post(
            f"{s.guard_url.rstrip('/')}/check",
            json={"message": message, "direction": direction},
            timeout=s.guard_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        log.error("เรียก Guard ไม่ได้: %s", exc)
        raise GuardUnavailable() from exc
    if resp.status_code != 200:
        log.error("Guard ตอบ %s: %s", resp.status_code, resp.text[:200])
        raise GuardUnavailable()
    data = resp.json()
    return GuardVerdict(
        safe=data.get("result") == "safe",
        msg=data.get("msg", ""),
        categories=data.get("categories", []),
    )
