"""ตัวเลขและวันที่แบบไทยสำหรับหมายเรียก (SPEC_summons_system: thai_format.py)."""

from datetime import date

from core.thai import MONTHS, to_be, to_thai_digits

__all__ = ["thai_digits", "thai_month", "be_year", "be_year_2", "thai_date", "to_thai_digits"]


def thai_digits(value) -> str:
    return to_thai_digits(value)


def thai_month(d: date) -> str:
    return MONTHS[d.month - 1]


def be_year(d: date) -> int:
    return to_be(d.year)


def be_year_2(d: date, digits: bool = True) -> str:
    """2 หลักท้ายของ พ.ศ. (แบบฟอร์มพิมพ์ "พ.ศ. ๒๕" ไว้แล้ว): 2026 → ๖๙."""
    s = f"{be_year(d) % 100:02d}"
    return to_thai_digits(s) if digits else s


def thai_date(d: date, digits: bool = True) -> str:
    """2026-02-09 → ๙ กุมภาพันธ์ ๒๕๖๙."""
    s = f"{d.day} {thai_month(d)} {be_year(d)}"
    return to_thai_digits(s) if digits else s
