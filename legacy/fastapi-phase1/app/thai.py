"""Thai formatting helpers: Thai digits, Buddhist-era dates, number words.

Screen text uses Arabic digits and short BE dates; documents use Thai digits
and full BE dates, following the unit's summons design system.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
          "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
MONTHS_SHORT = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
_TH_DIGITS = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")
_AR_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def to_thai_digits(value) -> str:
    return str(value).translate(_TH_DIGITS)


def to_arabic_digits(value: str) -> str:
    return str(value).translate(_AR_DIGITS)


def to_be(year: int) -> int:
    return year + 543 if year < 2400 else year


def from_be(year: int) -> int:
    return year - 543 if year > 2400 else year


def format_date(d: date | datetime | None, style: str = "short") -> str:
    """short: 12 พ.ย. 2569 · long: 12 พฤศจิกายน 2569 · doc: ๑๒ เดือน พฤศจิกายน พ.ศ. ๒๕๖๙"""
    if d is None:
        return ""
    y = to_be(d.year)
    if style == "long":
        return f"{d.day} {MONTHS[d.month - 1]} {y}"
    if style == "doc":
        return f"{to_thai_digits(d.day)} เดือน {MONTHS[d.month - 1]} พ.ศ. {to_thai_digits(y)}"
    return f"{d.day} {MONTHS_SHORT[d.month - 1]} {y}"


def format_time(t: datetime | str, doc: bool = False) -> str:
    if isinstance(t, datetime):
        t = t.strftime("%H:%M")
    hh, mm = t.split(":")[:2]
    s = f"{int(hh):02d}.{mm}"
    return f"{to_thai_digits(s)} นาฬิกา" if doc else f"{s} น."


def format_datetime(d: datetime | None) -> str:
    return f"{format_date(d)} {format_time(d)}" if d else ""


def parse_be_date(text: str) -> date:
    """Parse '25/11/2569', '2569-11-25' or '2026-11-25' into a date (CE)."""
    t = to_arabic_digits(text.strip())
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        d, mo, y = map(int, m.groups())
        return date(from_be(y), mo, d)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
        return date(from_be(y), mo, d)
    raise ValueError(f"อ่านวันที่ไม่ได้: {text}")


def add_minutes(hhmm: str, minutes: int) -> str:
    h, m = map(int, hhmm.split(":"))
    total = (h * 60 + m + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def mmss(seconds: float) -> str:
    s = int(round(seconds))
    if s >= 3600:
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def mask_citizen_id(cid: str | None) -> str:
    digits = re.sub(r"\D", "", to_arabic_digits(cid or ""))
    if len(digits) != 13:
        return cid or ""
    return f"{digits[0]}-xxxx-xxxxx-{digits[10:12]}-{digits[12]}"


def mask_account(acc: str | None) -> str:
    digits = re.sub(r"\D", "", acc or "")
    if len(digits) < 6:
        return acc or ""
    return f"xxx-x-x{digits[-5:-1]}-x"


# ---------- Thai number words (for checking figures against speech) ----------
_UNITS = {"ศูนย์": 0, "หนึ่ง": 1, "เอ็ด": 1, "สอง": 2, "ยี่": 2, "สาม": 3, "สี่": 4,
          "ห้า": 5, "หก": 6, "เจ็ด": 7, "แปด": 8, "เก้า": 9}
_MULT = {"สิบ": 10, "ร้อย": 100, "พัน": 1000, "หมื่น": 10_000, "แสน": 100_000}
_WORD_RE = re.compile("(?:" + "|".join(sorted(list(_UNITS) + list(_MULT) + ["ล้าน"], key=len, reverse=True)) + ")+")
_TOKEN_RE = re.compile("|".join(sorted(list(_UNITS) + list(_MULT) + ["ล้าน"], key=len, reverse=True)))


def thai_words_to_int(text: str) -> int | None:
    tokens = _TOKEN_RE.findall(text)
    if not tokens or "".join(tokens) != text:
        return None
    total, chunk, digit = 0, 0, None
    for tok in tokens:
        if tok in _UNITS:
            digit = _UNITS[tok]
        elif tok in _MULT:
            mult = _MULT[tok]
            chunk += (1 if digit is None else digit) * mult
            digit = None
        else:  # ล้าน
            chunk += digit or 0
            total = (total + chunk) * 1_000_000
            chunk, digit = 0, None
    return total + chunk + (digit or 0)


def numbers_in_speech(text: str) -> set[int]:
    """All numbers said in a transcript, whether written as digits or Thai words."""
    found: set[int] = set()
    t = to_arabic_digits(text)
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", t):
        raw = m.group().replace(",", "")
        try:
            found.add(int(float(raw)))
        except ValueError:
            pass
    for m in _WORD_RE.finditer(t):
        n = thai_words_to_int(m.group())
        if n is not None:
            found.add(n)
    return found


def numbers_in_draft(text: str) -> list[tuple[str, int]]:
    """Figures written in a draft answer (digits only), with their literal text."""
    out = []
    t = to_arabic_digits(text)
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", t):
        raw = m.group()
        try:
            out.append((raw, int(float(raw.replace(",", "")))))
        except ValueError:
            pass
    return out


def plus_days(d: date, n: int) -> date:
    return d + timedelta(days=n)
