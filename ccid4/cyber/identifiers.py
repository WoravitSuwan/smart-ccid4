"""ตัวระบุสำหรับเชื่อมโยงข้ามคดี (B4): normalize → HMAC → เทียบ.

The index keeps only a keyed hash and a masked label, never the raw value: the same account written
"123-4-56789-0" in one case and "1234567890" in another gives the same hash, and the index itself
reveals nothing if copied.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from urllib.parse import urlsplit

from django.conf import settings

from core import thai

KINDS = {
    "bank_account": "เลขบัญชี",
    "phone": "เบอร์โทร",
    "citizen_id": "เลขบัตรประชาชน",
    "social": "บัญชีโซเชียล/LINE ID",
    "url": "URL",
    "domain": "โดเมน",
    "email": "อีเมล",
    "crypto": "กระเป๋าคริปโต",
}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", thai.to_arabic_digits(value or ""))


def normalize(kind: str, value: str) -> str:
    """Comparable form, or "" when the value is too short to link cases safely."""
    v = thai.to_arabic_digits(value or "").strip()
    if not v:
        return ""
    if kind == "bank_account":
        d = _digits(v)
        return d if 6 <= len(d) <= 20 else ""
    if kind == "phone":
        d = _digits(v)
        if d.startswith("0066"):
            d = "0" + d[4:]
        elif d.startswith("66") and len(d) in (10, 11):
            d = "0" + d[2:]
        return d if len(d) in (9, 10) and d.startswith("0") else ""
    if kind == "citizen_id":
        d = _digits(v)
        return d if len(d) == 13 else ""
    if kind == "social":
        s = re.sub(r"\s+", "", v.lower()).lstrip("@")
        s = re.sub(r"^(?:line|lineid|line id|ไลน์|fb|facebook|ig|tiktok|telegram|tg)[:：]", "", s)
        return s if len(s) >= 3 else ""
    if kind == "email":
        s = v.lower().replace(" ", "")
        return s if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", s) else ""
    if kind in ("url", "domain"):
        s = v.lower()
        if "://" not in s:
            s = "http://" + s
        parts = urlsplit(s)
        host = (parts.hostname or "").removeprefix("www.")
        if "." not in host:
            return ""
        if kind == "domain":
            return host
        path = parts.path.rstrip("/")
        return host + path + (f"?{parts.query}" if parts.query else "")
    if kind == "crypto":
        s = v.replace(" ", "")
        if s.lower().startswith(("0x", "bc1", "tb1")):
            s = s.lower()  # hex and bech32 are case-insensitive; base58 (BTC legacy, TRON) is not
        return s if len(s) >= 25 else ""
    raise ValueError(kind)


def digest(kind: str, norm: str) -> str:
    key = (
        getattr(settings, "IDENTIFIER_KEY", "") or hashlib.sha256(f"ccid4-identifier:{settings.SECRET_KEY}".encode()).hexdigest()
    )
    return hmac.new(key.encode(), f"{kind}:{norm}".encode(), hashlib.sha256).hexdigest()


def mask(kind: str, norm: str) -> str:
    if kind == "bank_account":
        return thai.mask_account(norm)
    if kind == "phone":
        return f"{norm[:3]}-xxx-{norm[-4:]}"
    if kind == "citizen_id":
        return thai.mask_citizen_id(norm)
    if kind in ("social", "email"):
        name, _, host = norm.partition("@")
        return f"{name[:2]}•••" + (f"@{host}" if host else "")
    if kind == "crypto":
        return f"{norm[:6]}…{norm[-4:]}"
    return norm[:80]  # URLs and domains of scam websites are shown as they are


def entry(kind: str, value: str) -> tuple[str, str] | None:
    """(digest, masked label) or None."""
    norm = normalize(kind, value)
    return (digest(kind, norm), mask(kind, norm)) if norm else None
