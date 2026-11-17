"""รหัสไปรษณีย์จาก ตำบล + อำเภอ + จังหวัด (ข้อมูลเปิด thai-province-data, MIT) — อ่านจากไฟล์ในเครื่องเท่านั้น."""

import csv
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "thai_postcodes.csv"
PREFIXES = re.compile(r"^(?:ตำบล|ต\.|แขวง|อำเภอ|อ\.|เขต|จังหวัด|จ\.)\s*")
BANGKOK = {"กรุงเทพ", "กรุงเทพฯ", "กทม", "กทม.", "กรุงเทพมหานคร", "bangkok"}


def norm(name: str) -> str:
    name = re.sub(r"\s+", "", (name or "").strip())
    name = PREFIXES.sub("", name)
    return "กรุงเทพมหานคร" if name.lower() in BANGKOK else name


@lru_cache(maxsize=1)
def table() -> tuple[dict, dict]:
    full, by_district = {}, {}
    with DATA.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["province"], r["district"], r["subdistrict"])
            full[key] = r["postcode"]
            by_district.setdefault((r["province"], r["district"]), set()).add(r["postcode"])
    return full, by_district


def lookup(tambon: str, amphoe: str, province: str) -> str:
    """Postcode or "" when the place is unknown or ambiguous."""
    full, by_district = table()
    p, a, t = norm(province), norm(amphoe), norm(tambon)
    if not p or not a:
        return ""
    candidates = [a] + ([f"เมือง{p}"] if a == "เมือง" else [])
    for district in candidates:
        if t and (p, district, t) in full:
            return full[(p, district, t)]
        codes = by_district.get((p, district))
        if codes and len(codes) == 1:
            return next(iter(codes))
    return ""
