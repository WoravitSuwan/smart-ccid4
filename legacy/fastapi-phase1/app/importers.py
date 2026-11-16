"""Read the unit's case register (Excel/CSV) for batch summons.

Column headers vary between units, so they are matched by keywords.
Rows whose address is incomplete are kept but marked, so they can be fixed
instead of silently producing undeliverable summons.
"""
from __future__ import annotations

import csv
import io
import re

from openpyxl import load_workbook

COLUMNS = {
    "case_no": ["เลขคดี", "คดีอาญาที่", "เลขที่คดี", "case"],
    "receipt_no": ["เลขรับแจ้ง", "เลขแจ้งความ", "receipt"],
    "charge": ["ข้อหา", "ฐานความผิด", "charge"],
    "complainant": ["ผู้กล่าวหา", "ผู้แจ้ง", "ผู้เสียหาย", "ชื่อ-สกุล", "ชื่อ"],
    "address": ["ที่อยู่", "address"],
    "postcode": ["รหัสไปรษณีย์", "ไปรษณีย์", "postcode", "zip"],
    "investigator": ["พนักงานสอบสวน", "พงส", "ผู้รับผิดชอบ"],
    "status": ["สถานะ", "ผลคดี", "status"],
}
REQUIRED = ["case_no", "complainant", "address"]


def _match(header: str) -> str | None:
    h = re.sub(r"\s+", "", str(header or "")).lower()
    for key, words in COLUMNS.items():
        if any(w.lower() in h for w in words):
            return key
    return None


def _rows_from_bytes(data: bytes, filename: str) -> list[list]:
    if filename.lower().endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [row for row in csv.reader(io.StringIO(text))]
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ValueError("เปิดไฟล์ไม่ได้ ใช้ไฟล์ .xlsx หรือ .csv") from e
    ws = wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def read_register(data: bytes, filename: str) -> list[dict]:
    rows = _rows_from_bytes(data, filename)
    header_idx, mapping = None, {}
    for i, row in enumerate(rows[:10]):  # header may not be on the first line
        m = {}
        for j, cell in enumerate(row):
            key = _match(cell)
            if key and key not in m:
                m[key] = j
        if all(k in m for k in REQUIRED):
            header_idx, mapping = i, m
            break
    if header_idx is None:
        raise ValueError("ไม่พบหัวคอลัมน์ที่จำเป็น: เลขคดี ผู้กล่าวหา และที่อยู่")
    out = []
    for n, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        def val(key):
            j = mapping.get(key)
            v = row[j] if j is not None and j < len(row) else None
            return "" if v is None else str(v).strip()
        if not any(val(k) for k in REQUIRED):
            continue
        rec = {k: val(k) for k in COLUMNS}
        rec["row_no"] = n
        if not rec["postcode"]:
            m = re.search(r"\b(\d{5})\b", rec["address"])
            if m:
                rec["postcode"] = m.group(1)
                rec["address"] = rec["address"].replace(m.group(1), "").strip()
        problems = []
        if not rec["address"] or not re.search(r"\d", rec["address"]):
            problems.append("ขาดบ้านเลขที่")
        if not rec["postcode"]:
            problems.append("ขาดรหัสไปรษณีย์")
        rec["problem"] = " · ".join(problems)
        out.append(rec)
    if not out:
        raise ValueError("ไม่พบแถวข้อมูลใต้หัวคอลัมน์")
    return out
