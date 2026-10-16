"""ชั้นกฎของร้าน (Business Rules) — ตรวจสิ่งที่ Llama Guard ไม่ได้ออกแบบมาให้จับ

Llama Guard 4 จับเนื้อหาอันตรายทั่วไป (S1–S14) แต่คำขอเช่น "ขอลดเหลือ 200",
"ขอเบอร์ลูกค้าคนที่ซ่อมเมื่อวาน", "ขอประกันเพิ่มเป็น 1 ปี" ไม่ใช่เนื้อหาอันตรายในสายตาโมเดล
จึงต้องมีกฎของร้านแยกออกมา ตรวจแบบ deterministic (regex) ก่อนเรียกโมเดล

ฝั่ง input (ข้อความลูกค้า)
  BIZ_INJECTION  พยายามสั่งให้ลืมคำสั่ง / ขอดู system prompt / อ้างตัวเป็นเจ้าของร้านหรือแอดมิน
  BIZ_PRIVACY    ขอข้อมูลของลูกค้าคนอื่น
  BIZ_POLICY     ขอขยาย/เปลี่ยนการรับประกันหรือนโยบายของร้าน
  BIZ_DISCOUNT   ขอส่วนลด / ต่อราคา / ขอไม่คิดค่าบริการ

ฝั่ง output (คำตอบของบอท) — ใช้ข้อมูลจากไฟล์คลังความรู้ (KNOWLEDGE_PATH)
  BIZ_PII_LEAK      คำตอบมีเบอร์โทร / อีเมล / เลข 13 หลัก ที่ไม่ใช่ข้อมูลของร้านในคลังความรู้
  BIZ_DISCOUNT      คำตอบสัญญาว่าจะลดราคา (เช่น "ลด 20%")
  BIZ_PRICE_NOT_IN_KB  คำตอบมีราคา (xxx บาท) ที่ไม่มีอยู่ในคลังความรู้ = บอทแต่งราคาเอง

ลดผลบวกลวงของ Llama Guard (service_override)
  Llama Guard มักตีความงานบริการของร้านเป็นอาชญากรรม (S2) เช่น "เผลอลบไฟล์ กู้ข้อมูลได้ไหม"
  หรือคำตอบที่อธิบายว่าป๊อปอัปไวรัสเป็นการหลอกลวง จึงยอมให้ผ่านเฉพาะกรณีที่ครบทุกข้อ:
  โมเดลให้แค่หมวด S2 + ข้อความพูดถึงบริการของร้าน + ไม่มีเจตนาโจมตี/ละเมิดใด ๆ
"""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("nvd-guard")

# คำที่บ่งบอกว่าเป็น "คำขอ" ไม่ใช่แค่คำถามข้อมูล
# เช่น "ประกัน 3 เดือนใช่ไหม" (ถามข้อมูล → ผ่าน) vs "ขอประกัน 1 ปีได้ไหม" (ขอเปลี่ยน → บล็อก)
_REQUEST_MARKER = re.compile(
    r"ขอ|ได้(?:ไหม|มั้ย|ป่ะ|ปะ|ป้ะ|ไม|บ่|หรือเปล่า|รึเปล่า|เปล่า)|หน่อย|ให้ด้วย|please|can you|could you",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Rule:
    code: str
    patterns: tuple[re.Pattern, ...]
    needs_request: bool = False  # ต้องมีคำแสดงการขอร่วมด้วยถึงจะนับว่าผิดกฎ

    def matches(self, text: str) -> bool:
        # ตรวจทั้งข้อความเดิมและแบบตัดช่องว่างทิ้ง (กันการเว้นวรรคหลบ เช่น "ส่วน ลด")
        compact = re.sub(r"\s+", "", text)
        hit = any(p.search(text) or p.search(compact) for p in self.patterns)
        if not hit:
            return False
        return not self.needs_request or bool(_REQUEST_MARKER.search(text))


def _p(*patterns: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_INPUT_RULES: list[Rule] = [
    Rule("BIZ_INJECTION", _p(
        r"ignore\s+(all\s+|the\s+)?(previous|above|prior)",
        r"disregard\s+(all\s+|the\s+)?(previous|above|prior|instructions)",
        r"system\s*prompt|developer\s*mode|jailbreak|\bDAN\b",
        r"(ลืม|เพิกเฉย|ไม่ต้องสนใจ|ละเว้น|ยกเลิก|ข้าม)\S{0,10}(คำสั่ง|กฎ|ข้อกำหนด|prompt|พรอมต์|บทบาท)",
        r"(แสดง|บอก|เปิดเผย|พิมพ์|ขอดู)\S{0,10}(คำสั่งระบบ|พรอมต์|prompt|คำสั่งที่ได้รับ|คำสั่งตั้งต้น)",
        r"(ผม|ฉัน|เรา|หนู|กู)(คือ|เป็น)(เจ้าของร้าน|ผู้จัดการ|แอดมิน|admin|ผู้ดูแลระบบ|developer|นักพัฒนา|ช่างของร้าน)",
        r"(สมมติว่า|ทำตัว|สวมบทบาท|แกล้งทำ)\S{0,8}เป็น(เจ้าของร้าน|ผู้จัดการ|แอดมิน|admin|ผู้ดูแล|AIที่ไม่มี)",
    )),
    Rule("BIZ_PRIVACY", _p(
        r"(เบอร์|โทรศัพท์|ชื่อ|ที่อยู่|อีเมล|ข้อมูล|ประวัติ|ไลน์|line)\S{0,8}(ของ)?(ลูกค้า|คนอื่น|คนที่มา|คนที่ซ่อม|คนก่อน)",
        r"ลูกค้า\S{0,10}(คนอื่น|คนก่อน|คนที่|รายอื่น|ท่านอื่น|เมื่อวาน|เมื่อกี้)",
        r"ใคร\S{0,10}(มาซ่อม|เอาเครื่องมา|ซ่อมอะไร)",
        r"(customer|client)s?\W{0,3}(phone|address|email|name|data|info)",
    )),
    Rule("BIZ_POLICY", _p(
        r"(ขยาย|ต่อ|เพิ่ม|ยืด)\S{0,6}(ระยะ)?(เวลา)?(รับ)?ประกัน",
        r"ประกัน\S{0,20}(เพิ่ม|นานขึ้น|ยาวขึ้น|\d+(ปี|เดือน))",
        r"(เปลี่ยน|แก้|ยกเว้น)\S{0,4}(นโยบาย|เงื่อนไข|กฎ)(ให้|ของร้าน)?",
        r"ยกเว้นให้",
        r"คืนเงิน\S{0,10}(ค่าอะไหล่|เกิน|มากกว่า|\d{3,}%)",
    ), needs_request=True),
    Rule("BIZ_DISCOUNT", _p(
        r"ส่วนลด|ลดราคา|ต่อราคา|ราคาพิเศษ|discount|cheaper",
        r"(ขอ|ช่วย)ลด(ราคา|ให้|หน่อย|ได้|อีก|เหลือ|\d)",
        r"ลด(ให้|หน่อย|อีก)",
        r"ลดได้(ไหม|มั้ย|ป่ะ|ปะ|ไม|บ้าง)",
        r"ลด\S{0,6}เหลือ",
        r"เหลือ\d[\d,]*(บาท)?(ได้|นะ)",
        r"(ซ่อม|ลง|ทำ)ให้ฟรี",
    )),
    # "ฟรี/ไม่คิดค่า..." นับเป็นการขอส่วนลดเฉพาะเมื่อมีคำขอร่วมด้วย
    # "ตรวจเช็คไม่คิดค่าบริการใช่ไหม" = ถามข้อมูล → ผ่านไปให้ RAG ตอบ
    Rule("BIZ_DISCOUNT", _p(
        r"(ฟรี|ไม่คิด|ไม่เก็บ)ค่า(แรง|บริการ|อะไหล่|ซ่อม)",
    ), needs_request=True),
]

_OUTPUT_DISCOUNT = _p(
    r"ลด(ราคา)?(ให้)?\s*\d+\s*(%|เปอร์เซ็นต์|บาท)",
    r"ส่วนลด\s*\d",
    r"discount\s*\d",
)

_PRICE_RE = re.compile(r"(?:฿\s*(\d{1,3}(?:,\d{3})+|\d+))|(?:(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:บาท|฿))")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[-\s]?\d{3}[-\s]?\d{3,4}(?!\d)")
# เบอร์ต่างประเทศ / toll-free เช่น 1-800-000-0000, +1 (800) 000-0000 (มักพบในป๊อปอัปหลอกลวง)
_PHONE_INTL_RE = re.compile(r"(?<![\d-])\+?\d{1,3}[-\s]\(?\d{3}\)?[-\s]\d{3}[-\s]\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_ID13_RE = re.compile(r"(?<!\d)\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d(?!\d)")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def extract_prices(text: str) -> set[int]:
    out: set[int] = set()
    for m in _PRICE_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        out.add(int(raw.replace(",", "")))
    return out


# ถามช่องทางติดต่อ/ที่ตั้งของร้านเอง (ข้อมูลสาธารณะ) — ใช้ยกเลิกผล S7 ของ Llama Guard
_SHOP_CONTACT_ASK = _p(
    r"(เบอร์|โทร|ติดต่อ|ไลน์|line|อีเมล|email|ที่อยู่|ที่ตั้ง|แผนที่)\S{0,15}(ร้าน|rebooter)",
    r"(ร้าน|rebooter)\S{0,15}(เบอร์|โทร|ติดต่อ|ไลน์|line|อีเมล|email|ที่อยู่|ที่ตั้ง|อยู่ที่ไหน|อยู่ไหน)",
)
# อ้างถึงบุคคลอื่น → ไม่ยกเลิกการบล็อก
_OTHER_PERSON = _p(r"ลูกค้า(คน|ท่าน)?(อื่น|นั้น|ที่)", r"คนอื่น", r"ของ(เขา|เธอ|เพื่อน|แฟน)", r"เบอร์ส่วนตัว", r"บ้านช่าง|ช่าง\S{0,6}(เบอร์|บ้าน)")


class KnowledgeFacts:
    """อ่านราคา / เบอร์โทร / อีเมลที่ "อนุญาต" จากไฟล์คลังความรู้ของร้าน
    รีโหลดเองเมื่อไฟล์ถูกแก้ (เหมือน blocklist) — ถ้าไม่ได้ตั้ง path จะปิดการตรวจฝั่ง output ที่ต้องใช้ KB"""

    def __init__(self, path: str):
        self.path = Path(path) if path else None
        self._mtime: float | None = None
        self.prices: set[int] = set()
        self.phones: set[str] = set()
        self.emails: set[str] = set()

    @property
    def loaded(self) -> bool:
        self._reload_if_changed()
        return self._mtime is not None

    def _reload_if_changed(self) -> None:
        if self.path is None:
            return
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError:
            if self._mtime is not None:
                log.warning("อ่านคลังความรู้ไม่ได้: %s (ปิดการตรวจราคา)", self.path)
            self._mtime = None
            return
        if mtime == self._mtime:
            return
        text = self.path.read_text(encoding="utf-8-sig")
        self.prices = extract_prices(text)
        self.phones = {_digits(m) for m in _PHONE_RE.findall(text)}
        self.emails = {e.casefold() for e in _EMAIL_RE.findall(text)}
        self._mtime = mtime
        log.info("โหลดคลังความรู้: ราคา %d ค่า, เบอร์ร้าน %d เบอร์", len(self.prices), len(self.phones))


class BusinessRules:
    def __init__(self, knowledge_path: str = ""):
        self.kb = KnowledgeFacts(knowledge_path)

    def check(self, message: str, direction: str) -> str | None:
        """คืนรหัสกฎที่ผิด (เช่น "BIZ_DISCOUNT") หรือ None ถ้าผ่าน"""
        if direction == "input":
            return self._check_input(message)
        return self._check_output(message)

    def contact_override(self, text: str, codes: list[str], direction: str) -> bool:
        """True = Llama Guard ให้ S7 (Privacy) กับการถาม/ให้ช่องทางติดต่อของร้านเอง ซึ่งเป็นข้อมูลสาธารณะในคลังความรู้"""
        if not codes or not set(codes) <= {"S7"}:
            return False
        compact = re.sub(r"\s+", "", text)
        if any(p.search(text) or p.search(compact) for p in _OTHER_PERSON):
            return False
        if direction == "input":
            return any(p.search(text) or p.search(compact) for p in _SHOP_CONTACT_ASK)
        # ขาออก: ทุกเบอร์/อีเมลในคำตอบต้องเป็นของร้านตามคลังความรู้ และต้องมีอย่างน้อยหนึ่งรายการ
        if not self.kb.loaded or _ID13_RE.search(text):
            return False
        phones = [_digits(x) for x in _PHONE_RE.findall(text) + _PHONE_INTL_RE.findall(text)]
        emails = [e.casefold() for e in _EMAIL_RE.findall(text)]
        has_line = bool(re.search(r"@rebooter\b", text, re.IGNORECASE))
        if not (phones or emails or has_line):
            return False
        return all(p in self.kb.phones for p in phones) and all(e in self.kb.emails for e in emails)

    def _check_input(self, text: str) -> str | None:
        for rule in _INPUT_RULES:
            if rule.matches(text):
                return rule.code
        return None

    def _check_output(self, text: str) -> str | None:
        kb_ready = self.kb.loaded

        # 1) ข้อมูลส่วนบุคคลหลุดในคำตอบ
        if _ID13_RE.search(text):
            return "BIZ_PII_LEAK"
        if kb_ready:
            for phone in _PHONE_RE.findall(text) + _PHONE_INTL_RE.findall(text):
                if _digits(phone) not in self.kb.phones:
                    return "BIZ_PII_LEAK"
            for email in _EMAIL_RE.findall(text):
                if email.casefold() not in self.kb.emails:
                    return "BIZ_PII_LEAK"

        # 2) บอทสัญญาส่วนลด
        compact = re.sub(r"\s+", "", text)
        if any(p.search(text) or p.search(compact) for p in _OUTPUT_DISCOUNT):
            return "BIZ_DISCOUNT"

        # 3) ราคาที่ไม่มีในคลังความรู้ (บอทแต่งราคาเอง)
        if kb_ready:
            unknown = extract_prices(text) - self.kb.prices
            if unknown:
                log.info("พบราคาที่ไม่มีในคลังความรู้: %s", sorted(unknown))
                return "BIZ_PRICE_NOT_IN_KB"
        return None


# ---------- ลดผลบวกลวงของ Llama Guard ----------
# หมวดที่ยอมพิจารณาใหม่: S2 (Non-Violent Crimes) เท่านั้น หมวดอื่นบล็อกตามโมเดลเสมอ
OVERRIDABLE_CODES = frozenset({"S2"})

# คำที่บ่งบอกว่าเป็นอาการ/บริการที่ร้านรับทำ (S04 กู้ข้อมูล, S11 ไวรัส, S17 สำรองข้อมูล)
_SERVICE_CONTEXT = _p(
    r"กู้(ข้อมูล|ไฟล์|รูป|งาน)", r"(ลบ|ฟอร์แมต|format)\S{0,12}(ไฟล์|ข้อมูล|ไดรฟ์|drive|พลาด|ผิด)",
    r"เผลอ\S{0,6}(ลบ|ฟอร์แมต)", r"ข้อมูล(หาย|สูญหาย)", r"ไฟล์(หาย|เสีย)", r"สำรองข้อมูล|backup",
    r"ไวรัส|มัลแวร์|แอดแวร์|malware|adware|virus", r"ป๊อปอัป|ป็อปอัพ|popup|pop-up|เด้ง",
    r"หลอกลวง|มิจฉาชีพ|scam|tech\s*support",
)

# เจตนาโจมตีหรือละเมิด: ถ้าพบ ห้ามยกเลิกการบล็อกเด็ดขาด
_ATTACK_INTENT = _p(
    r"แฮ[กค]|hack|เจาะ(ระบบ|รหัส|เข้า)|crack|แคร็ก|keygen|bypass|ข้ามการ(ยืนยัน|ล็อก)",
    r"(สร้าง|เขียน|ทำ|ปล่อย|ส่ง)\S{0,6}(ไวรัส|มัลแวร์|malware|virus|ransomware|แรนซัมแวร์)",
    r"ขโมย|ดัก(จับ|ฟัง|รหัส)|keylogger|phishing|ฟิชชิ่ง|สปาย|spyware|แอบ(ดู|ส่อง|เข้า)",
    r"(รหัส|password|บัญชี|ข้อมูล|ไฟล์)\S{0,15}(คนอื่น|ของเพื่อน|ของแฟน|ข้างห้อง|หอพัก|บริษัทอื่น)",
    r"ลบ\S{0,10}(หลักฐาน|ประวัติ\S{0,6}(ตำรวจ|คดี))|ทำลายหลักฐาน",
    r"เถื่อน|ละเมิดลิขสิทธิ์|ปลดล็อก\S{0,10}(ไม่จ่าย|ฟรี)",
)


def service_override(text: str, codes: list[str]) -> bool:
    """True = ผลไม่ปลอดภัยจาก Llama Guard เป็นผลบวกลวงจากงานบริการของร้าน ให้ถือว่าปลอดภัย"""
    if not codes or not set(codes) <= OVERRIDABLE_CODES:
        return False
    compact = re.sub(r"\s+", "", text)
    hit = lambda pats: any(p.search(text) or p.search(compact) for p in pats)  # noqa: E731
    return hit(_SERVICE_CONTEXT) and not hit(_ATTACK_INTENT)
