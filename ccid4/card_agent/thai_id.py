"""อ่านบัตรประชาชนไทย (smart card) — คำสั่ง APDU และการแยกข้อมูล.

Pure functions (no reader needed) so they can be tested; agent.py talks to the reader with pyscard.
The card's applet and its read commands are the publicly documented layout of the Thai national ID card.
"""

from __future__ import annotations

import re
from datetime import date

SELECT_APPLET = [0x00, 0xA4, 0x04, 0x00, 0x08, 0xA0, 0x00, 0x00, 0x00, 0x54, 0x48, 0x00, 0x01]
FIELDS = {  # name: (P1, P2, length)
    "cid": (0x00, 0x04, 0x0D),
    "name_th": (0x00, 0x11, 0x64),
    "name_en": (0x00, 0x75, 0x64),
    "birth": (0x00, 0xD9, 0x08),
    "gender": (0x00, 0xE1, 0x01),
    "issuer": (0x00, 0xF6, 0x64),
    "issued": (0x01, 0x67, 0x08),
    "expires": (0x01, 0x6F, 0x08),
    "address": (0x15, 0x79, 0x64),
}
PHOTO_START, PHOTO_CHUNK, PHOTO_PARTS = 0x017B, 0xFF, 20


def read_command(name: str) -> list[int]:
    p1, p2, n = FIELDS[name]
    return [0x80, 0xB0, p1, p2, 0x02, 0x00, n]


def photo_commands() -> list[list[int]]:
    out = []
    for i in range(PHOTO_PARTS):
        offset = PHOTO_START + i * PHOTO_CHUNK
        out.append([0x80, 0xB0, (offset >> 8) & 0xFF, offset & 0xFF, 0x02, 0x00, PHOTO_CHUNK])
    return out


def get_response(atr: list[int], length: int) -> list[int]:
    """GET RESPONSE: cards whose ATR starts 3B 67 want class 00 C0 00 01, the others 00 C0 00 00."""
    p2 = 0x01 if atr[:2] == [0x3B, 0x67] else 0x00
    return [0x00, 0xC0, 0x00, p2, length]


def text(raw: bytes | list[int]) -> str:
    return bytes(raw).decode("tis_620", errors="replace").replace("\x00", "").strip()


def be_date(s: str) -> str:
    """'25300115' (B.E. yyyymmdd) → '1987-01-15'; unknown month/day ('00') → ''."""
    s = re.sub(r"\D", "", s or "")
    if len(s) != 8:
        return ""
    y, m, d = int(s[:4]) - 543, int(s[4:6]), int(s[6:8])
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ""


def split_name(raw: str) -> dict:
    """'นาย#สมชาย#กลาง#ใจดี' or 'นาย#สมชาย##ใจดี' → title / first / middle / last."""
    parts = [p.strip() for p in (raw or "").split("#")]
    parts += [""] * (4 - len(parts))
    return {"title": parts[0], "first_name": parts[1], "middle_name": parts[2], "last_name": parts[3]}


ADDRESS_KEYS = [
    ("moo", r"^(?:หมู่ที่|หมู่)\s*"), ("soi", r"^(?:ซอย|ซ\.)\s*"), ("road", r"^(?:ถนน|ถ\.)\s*"),
    ("tambon", r"^(?:ตำบล|แขวง|ต\.)\s*"), ("amphoe", r"^(?:อำเภอ|เขต|อ\.)\s*"), ("province", r"^(?:จังหวัด|จ\.)\s*"),
]  # fmt: skip


def split_address(raw: str) -> dict:
    """'99/1#หมู่ที่ 4##ซอยสุขใจ#ถนนสมมติ#ตำบลบางรัก#อำเภอเมือง#จังหวัดสมมติ' → house_no, moo, soi, road, tambon, amphoe, province."""
    out = {"house_no": "", "moo": "", "building": "", "soi": "", "road": "", "tambon": "", "amphoe": "", "province": ""}
    parts = [p.strip() for p in (raw or "").split("#") if p.strip()]
    if not parts:
        return out
    if not any(re.match(rx, parts[0]) for _, rx in ADDRESS_KEYS):
        out["house_no"] = parts.pop(0)
    for p in parts:
        if re.match(r"^กรุงเทพ", p):  # Bangkok addresses print the city without "จังหวัด"
            out["province"] = "กรุงเทพมหานคร"
            continue
        for key, rx in ADDRESS_KEYS:
            if re.match(rx, p):
                out[key] = re.sub(rx, "", p).strip()
                break
        else:
            out["building"] = (out["building"] + " " + p).strip()  # หมู่บ้าน/อาคาร or anything unlabelled
    if out["province"] in ("กรุงเทพมหานคร", "กรุงเทพฯ"):
        out["province"] = "กรุงเทพมหานคร"
    return out


def valid_cid(cid: str) -> bool:
    d = re.sub(r"\D", "", cid or "")
    if len(d) != 13:
        return False
    check = (11 - sum(int(d[i]) * (13 - i) for i in range(12)) % 11) % 10
    return check == int(d[12])


def to_person(raw: dict) -> dict:
    """Card fields → the fields of the case form (Person) + extra info shown in the review dialog."""
    name = split_name(raw.get("name_th", ""))
    en = split_name(raw.get("name_en", ""))
    cid = re.sub(r"\D", "", raw.get("cid", ""))
    gender = {"1": "ชาย", "2": "หญิง"}.get(raw.get("gender", "").strip(), "")
    first = name["first_name"] + (f" {name['middle_name']}" if name["middle_name"] else "")
    return {
        "title": name["title"], "first_name": first, "last_name": name["last_name"], "citizen_id": cid, "birth_date": be_date(raw.get("birth", "")),
        "gender": gender, **split_address(raw.get("address", "")), "name_en": " ".join(x for x in (en["title"], en["first_name"], en["last_name"]) if x),
        "issued": be_date(raw.get("issued", "")), "expires": be_date(raw.get("expires", "")), "issuer": raw.get("issuer", ""),
        "cid_valid": valid_cid(cid),
    }  # fmt: skip
