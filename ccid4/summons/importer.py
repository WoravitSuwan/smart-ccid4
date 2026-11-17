"""Import the two Excel files (ผู้แจ้ง + ทะเบียนคดีของกอง), joined by เลขรับแจ้ง. Re-importing updates, never duplicates."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

import openpyxl
from django.db import transaction
from django.utils import timezone

from audit.services import log

from . import postcodes
from .models import (
    ColumnMapping,
    Complainant,
    ImportBatch,
    ImportIssue,
    RegisterCase,
    RegisterInvestigator,
    RegisterStatus,
    RegisterUnit,
)

COMPLAINANT_COLUMNS = {
    "เลขรับแจ้ง": "receipt_no", "คำนำหน้าชื่อ": "title", "ชื่อ": "first_name", "นามสกุล": "last_name", "อายุ": "age",
    "เลขประจำตัวประชาชน": "citizen_id", "หมายเลขโทรศัพท์": "phone", "บ้านเลขที่": "house_no", "หมู่": "moo",
    "อาคาร/ตึก": "building", "ซอย": "soi", "ถนน": "road", "ตำบล": "tambon", "อำเภอ": "amphoe", "จังหวัด": "province",
    "รหัสไปรษณีย์": "postcode", "ประเภทคดี": "case_type_text",
}  # fmt: skip
COMPLAINANT_EXTRA = ["ที่อยู่อาศัย", "สถานภาพ", "ระดับการศึกษา", "บุคคลที่สาม", "อาชีพ", "สถานที่ทำงาน", "รายได้", "ชื่อบิดา",
                     "ชื่อมารดา", "จังหวัดที่อยู่ขณะที่แจ้ง", "สะดวกที่หน่วยใด"]  # fmt: skip
REGISTER_COLUMNS = {
    "ลำดับ": "seq", "เลขรับแจ้ง": "receipt_no", "เลขคดีอาญา": "case_no", "วันที่รับแจ้ง": "_day", "เดือนที่รับแจ้ง": "_month",
    "ปีที่รับแจ้ง": "_year", "ผู้เสียหาย": "victim_name", "เพศ": "gender", "อายุ": "age", "ประเภทคดี": "case_type",
    "ประเภทคดีระดับ 1": "type_l1", "ประเภทคดีระดับ 2": "type_l2", "ประเภทคดีระดับ 3": "type_l3", "ประเภทกลุ่มคดี": "case_group",
    "รายละเอียด": "detail", "มูลค่าความเสียหาย": "damage", "บช.": "bureau", "บก./ภ.จว.": "division", "หน่วยที่รับผิดชอบ": "_unit",
    "พนักงานสอบสวน": "_investigator", "พนักงานสืบสวน": "detective", "สถานะ": "_status", "เบอร์โทรศัพท์ผู้เสียหาย": "victim_phone",
    "เลขอ้างอิงธนาคาร": "bank_ref", "บันทึกการติดต่อ": "contact_log", "ประจำวัน": "daily_record", "หมายเหตุ": "note",
}  # fmt: skip
COLUMNS = {ImportBatch.Kind.COMPLAINANTS: COMPLAINANT_COLUMNS, ImportBatch.Kind.REGISTER: REGISTER_COLUMNS}
REQUIRED = {ImportBatch.Kind.COMPLAINANTS: ["receipt_no", "first_name"], ImportBatch.Kind.REGISTER: ["receipt_no", "_status"]}
SHEET_YEAR = re.compile(r"(\d{4})")


def norm_receipt(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def cell_text(value, field_name: str = "") -> str:
    """Excel cell → text. Numbers stay text (leading zeros of phones and IDs)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if field_name in ("phone", "victim_phone") and text.isdigit() and len(text) == 9 and not text.startswith("0"):
        text = "0" + text  # the sheet stored it as a number and dropped the 0
    if field_name == "citizen_id" and text.isdigit() and len(text) == 12:
        text = text.zfill(13)
    return text


# ---------------------------------------------------------------- workbook reading
def open_book(path):
    return openpyxl.load_workbook(path, read_only=True, data_only=True)


def detect_kind(book) -> str | None:
    for ws in book.worksheets:
        headers = {cell_text(c) for c in next(ws.iter_rows(max_row=1, values_only=True), ())}
        if {"ผู้เสียหาย", "สถานะ", "เลขรับแจ้ง"} <= headers:
            return ImportBatch.Kind.REGISTER
        if {"ชื่อ", "นามสกุล", "เลขรับแจ้ง"} <= headers:
            return ImportBatch.Kind.COMPLAINANTS
    return None


def headers_of(ws) -> list[str]:
    return [cell_text(c) for c in next(ws.iter_rows(max_row=1, values_only=True), ())]


def auto_mapping(kind: str, headers: list[str]) -> dict[str, str]:
    """header → field from the standard names, then from the mapping the user saved last time."""
    known = COLUMNS[kind]
    saved = ColumnMapping.objects.filter(kind=kind).first()
    saved_map = saved.mapping if saved else {}
    out = {}
    for h in headers:
        if h in known:
            out[h] = known[h]
        elif h in saved_map and saved_map[h] in known.values():
            out[h] = saved_map[h]
    return out


def missing_fields(kind: str, mapping: dict) -> list[str]:
    have = set(mapping.values())
    labels = {v: k for k, v in COLUMNS[kind].items()}
    return [labels[f] for f in REQUIRED[kind] if f not in have]


def rows(ws, mapping: dict, kind: str):
    """(row_no, {field: text}, {other header: text}) for rows that have a receipt number."""
    hdrs = headers_of(ws)
    for i, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        data, extra = {}, {}
        for h, v in zip(hdrs, values, strict=False):
            if h in mapping:
                data[mapping[h]] = cell_text(v, mapping[h])
            elif h and kind == ImportBatch.Kind.COMPLAINANTS:
                extra[h] = cell_text(v)
        data["receipt_no"] = norm_receipt(data.get("receipt_no"))
        if not data["receipt_no"]:
            continue  # blank trailing rows
        yield i, data, extra


# ---------------------------------------------------------------- analysis (preview and apply share it)
@dataclass
class Analysis:
    records: list = field(default_factory=list)  # (sheet, row_no, data, extra)
    issues: list = field(default_factory=list)  # (sheet, row_no, receipt, problem)
    new: int = 0
    updated: int = 0
    unmatched: int = 0
    changes: Counter = field(default_factory=Counter)

    @property
    def issue_rows(self) -> int:
        return len({(s, r) for s, r, _, _ in self.issues})

    def counts(self) -> dict:
        return {"rows": len(self.records), "new": self.new, "updated": self.updated, "issues": self.issue_rows,
                "unmatched": self.unmatched, "changes": dict(self.changes)}  # fmt: skip


def complainant_problems(d: dict) -> list[str]:
    out = []
    if not d.get("house_no"):
        out.append("ไม่มีบ้านเลขที่")
    if not d.get("province"):
        out.append("ไม่มีจังหวัด")
    if not d.get("tambon") or not d.get("amphoe"):
        out.append("ไม่มีตำบลหรืออำเภอ")
    cid = re.sub(r"\D", "", d.get("citizen_id", ""))
    if d.get("citizen_id") and len(cid) != 13:
        out.append("เลขบัตรไม่ใช่ 13 หลัก")
    return out


def analyze(batch: ImportBatch) -> Analysis:
    book = open_book(batch.file.path)
    a = Analysis()
    seen = Counter()
    kind = batch.kind
    sheets = batch.sheets or [ws.title for ws in book.worksheets]
    for ws in book.worksheets:
        if ws.title not in sheets:
            continue
        for row_no, data, extra in rows(ws, batch.mapping, kind):
            seen[data["receipt_no"]] += 1
            if seen[data["receipt_no"]] == 2:
                a.issues.append((ws.title, row_no, data["receipt_no"], "เลขรับแจ้งซ้ำในไฟล์"))
            if kind == ImportBatch.Kind.COMPLAINANTS:
                for p in complainant_problems(data):
                    a.issues.append((ws.title, row_no, data["receipt_no"], p))
            a.records.append((ws.title, row_no, data, extra))
    receipts = [d["receipt_no"] for _, _, d, _ in a.records]
    model = Complainant if kind == ImportBatch.Kind.COMPLAINANTS else RegisterCase
    other = RegisterCase if kind == ImportBatch.Kind.COMPLAINANTS else Complainant
    existing = {
        o.receipt_no: o
        for o in model.objects.filter(receipt_no__in=receipts).select_related(*(["status"] if model is RegisterCase else []))
    }
    matched = set(other.objects.filter(receipt_no__in=receipts).values_list("receipt_no", flat=True))
    for _, _, d, _ in {r[2]["receipt_no"]: r for r in a.records}.values():
        obj = existing.get(d["receipt_no"])
        if obj is None:
            a.new += 1
        else:
            a.updated += 1
            if kind == ImportBatch.Kind.REGISTER and (obj.status.name if obj.status else "") != d.get("_status", ""):
                a.changes["สถานะเปลี่ยน"] += 1
        if d["receipt_no"] not in matched:
            a.unmatched += 1
    return a


# ---------------------------------------------------------------- apply
def to_decimal(text: str) -> Decimal:
    try:
        return Decimal(str(text).replace(",", "") or 0)
    except InvalidOperation:
        return Decimal(0)


def report_date(d: dict) -> date | None:
    try:
        return date(int(d.get("_year")), int(d.get("_month")), int(d.get("_day")))  # the year column is ค.ศ.
    except (TypeError, ValueError):
        return None


def strip_rank(name: str) -> str:
    return re.sub(r"^(?:[ก-ฮ]{1,3}\.){2,4}", "", name or "").strip()


def match_user(name: str):
    from accounts.models import User

    bare = strip_rank(name)
    first, _, last = bare.partition(" ")
    if not first or not last:
        return None
    qs = User.objects.filter(first_name=first, last_name=last.strip(), is_active=True)
    return qs.first() if qs.count() == 1 else None


@transaction.atomic
def apply(batch: ImportBatch, user) -> dict:
    a = analyze(batch)
    if batch.kind == ImportBatch.Kind.COMPLAINANTS:
        _apply_complainants(a)
    else:
        _apply_register(a, user)
    ImportIssue.objects.filter(batch=batch).delete()
    ImportIssue.objects.bulk_create(
        [ImportIssue(batch=batch, sheet=s, row_no=r, receipt_no=n, problem=p) for s, r, n, p in a.issues]
    )
    batch.counts = a.counts()
    batch.status, batch.applied_at = ImportBatch.Status.DONE, timezone.now()
    batch.save()
    log(user, "import", "summons_import", batch.pk,
        f"นำเข้า{batch.get_kind_display()} {batch.file_name} ใหม่ {a.new} อัปเดต {a.updated} มีปัญหา {a.issue_rows}")  # fmt: skip
    return batch.counts


def _apply_complainants(a: Analysis) -> None:
    for _, _, d, extra in a.records:
        fields = {k: v for k, v in d.items() if k != "receipt_no"}
        fields["extra"] = {k: v for k, v in extra.items() if v}
        fields["issues"] = complainant_problems(d)
        fields["postcode_filled"] = (
            "" if d.get("postcode") else postcodes.lookup(d.get("tambon"), d.get("amphoe"), d.get("province"))
        )
        c, _ = Complainant.objects.update_or_create(receipt_no=d["receipt_no"], defaults=fields)
        RegisterCase.objects.filter(receipt_no=c.receipt_no, complainant__isnull=True).update(complainant=c)
        for rc in RegisterCase.objects.filter(receipt_no=c.receipt_no, case__isnull=False).select_related("case"):
            _ensure_accuser(rc.case, c)


def _apply_register(a: Analysis, user) -> None:
    statuses = {s.name: s for s in RegisterStatus.objects.all()}
    for _, _, d, _ in a.records:
        sname = d.get("_status", "").strip()
        status = statuses.get(sname)
        if sname and status is None:
            status = statuses[sname] = RegisterStatus.objects.create(name=sname, is_final=False, auto_created=True)
        unit = RegisterUnit.objects.get_or_create(name=d["_unit"])[0] if d.get("_unit") else None
        inv = None
        if d.get("_investigator"):
            inv, created = RegisterInvestigator.objects.get_or_create(name=d["_investigator"], defaults={"unit": unit})
            if inv.user_id is None and (u := match_user(inv.name)):
                inv.user = u
                inv.save(update_fields=["user"])
        fields = {k: v for k, v in d.items() if not k.startswith("_") and k not in ("receipt_no", "damage")}
        fields.update(damage=to_decimal(d.get("damage")), report_date=report_date(d), unit=unit, investigator=inv, status=status,
                      complainant=Complainant.objects.filter(receipt_no=d["receipt_no"]).first())  # fmt: skip
        rc, _ = RegisterCase.objects.update_or_create(receipt_no=d["receipt_no"], defaults=fields)
        _link_case(rc, user)


def _ensure_accuser(case, c: Complainant) -> None:
    from parties.models import CaseParty, Person

    if case.parties.filter(role="accuser").exists() or not (c.first_name or c.last_name):
        return
    cid = re.sub(r"\D", "", c.citizen_id)
    person = Person.objects.create(
        title=c.title, first_name=c.first_name or "-", last_name=c.last_name, citizen_id=cid if len(cid) == 13 else "",
        phone=c.phone, house_no=c.house_no[:40], moo=c.moo, building=c.building[:120], soi=c.soi[:120], road=c.road[:120],
        tambon=c.tambon, amphoe=c.amphoe, province=c.province, postcode=c.effective_postcode,
    )  # fmt: skip
    CaseParty.objects.create(case=case, person=person, role="accuser", order=1)


def _link_case(rc: RegisterCase, user) -> None:
    """เชื่อมแถวทะเบียนกับคดีใน CCID4 ด้วยเลขรับแจ้ง หรือสร้างคดีสถานะ 'นำเข้า' (B1)."""
    from cases.models import Case, CaseStatus, CaseType

    owner = (rc.investigator.user if rc.investigator and rc.investigator.user_id else None) or user
    case = rc.case or Case.objects.alive().filter(receipt_no=rc.receipt_no).first()
    if case is None:
        reported = timezone.make_aware(datetime.combine(rc.report_date, time(9, 0))) if rc.report_date else timezone.now()
        case = Case.objects.create(
            owner=owner, case_type=CaseType.ONLINE, status=CaseStatus.IMPORTED, receipt_no=rc.receipt_no, case_no=rc.case_no,
            title=rc.detail[:300], offense=rc.case_type[:400], circumstances=rc.detail, damage_amount=rc.damage, reported_at=reported,
        )  # fmt: skip
    elif rc.case_no and not case.case_no:
        case.case_no = rc.case_no
        case.save(update_fields=["case_no"])
    if rc.case_id != case.pk:
        rc.case = case
        rc.save(update_fields=["case"])
    if rc.complainant:
        _ensure_accuser(case, rc.complainant)
