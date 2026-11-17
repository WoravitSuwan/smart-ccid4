"""ออกหมายเรียกผู้กล่าวหา: ค่าที่เติม, ตรวจตาม ป.วิ.อ. ม.53, สร้างไฟล์ รวมไฟล์ ZIP และใบจ่าหน้าซอง."""

from __future__ import annotations

import copy
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from docx import Document
from docx.oxml.ns import qn

from audit.services import log
from core.convert import docx_to_pdf, soffice_available
from documents import engine

from . import thai_format as tf
from .models import RegisterCase, RegisterStatus, Summons, SummonsBatch, SummonsTemplate

ORIGINAL = Path(settings.BASE_DIR) / "sample_data" / "summons_template_sanitized.docx"
DEFAULT_REASON = "มาให้การต่อพนักงานสอบสวนตามประเด็นที่เกี่ยวข้องกับคดี"
ISSUED_STATUS = "ออกหมายเรียก"
SYNC_LIMIT = 20  # more than this runs in Celery with a progress bar


# ---------------------------------------------------------------- who sees / issues what
def visible(user):
    qs = RegisterCase.objects.select_related("complainant", "status", "investigator", "unit", "case")
    if user.role in ("clerk", "supervisor", "commander", "admin"):
        return qs
    return qs.filter(Q(investigator__user=user) | Q(case__owner=user)).distinct()


def can_issue(user, rc: RegisterCase | None = None) -> bool:
    if user.can("summons.batch") or user.role == "admin":
        return True
    if user.role in ("investigator", "assistant") and rc is not None:
        return (rc.investigator and rc.investigator.user_id == user.pk) or (rc.case and rc.case.owner_id == user.pk)
    return user.role in ("investigator", "assistant") and rc is None


# ---------------------------------------------------------------- templates
def build_v2(data: bytes) -> bytes:
    """แบบฟอร์มฉบับที่ 2: เติมตัวแทนวันนัด เดือนนัด ปีนัด เวลานัด สถานที่นัด เหตุที่เรียก สถานที่ออกหมาย (ไม่แก้ไฟล์ต้นฉบับ)."""
    doc = Document(io.BytesIO(data))
    body = doc.element.body

    def text(el):
        return "".join(t.text or "" for t in el.iter(qn("w:t")))

    def set_text(p, value, style_run=None):
        runs = p.findall(qn("w:r"))
        if runs:
            for t in runs[0].iter(qn("w:t")):
                t.text = ""
            first_t = next(runs[0].iter(qn("w:t")), None)
            if first_t is None:
                first_t = runs[0].makeelement(qn("w:t"), {})
                runs[0].append(first_t)
            first_t.text = value
            first_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            for r in runs[1:]:
                p.remove(r)
        else:
            r = copy.deepcopy(style_run) if style_run is not None else p.makeelement(qn("w:r"), {})
            for t in list(r.iter(qn("w:t"))):
                t.getparent().remove(t)
            t = r.makeelement(qn("w:t"), {})
            t.text = value
            r.append(t)
            p.append(r)

    paragraphs = list(body.iter(qn("w:p")))
    plus = next(p for p in paragraphs if text(p).strip() == "+14")
    style_run = plus.find(qn("w:r"))
    row = plus.getparent()
    while row.tag != qn("w:tr"):
        row = row.getparent()
    cells = [c for c in row if c.tag == qn("w:tc")]
    labels = [text(c).strip() for c in cells]
    set_text(plus, "{วันนัด}")
    for label, value in (("เดือน", "{เดือนนัด}"), ("พ.ศ. ๒๕", "{ปีนัด}")):
        i = next(i for i, lab in enumerate(labels) if lab.replace(" ", "") == label.replace(" ", ""))
        set_text(cells[i + 1].find(qn("w:p")), value, style_run)
    clock = next(c for c in cells if text(c).strip() == "๐๙.๐๐")
    set_text(clock.find(qn("w:p")), "{เวลานัด}")
    for p in paragraphs:
        t = text(p)
        if t.startswith("ไป ณ ที่"):
            runs = p.findall(qn("w:r"))
            for r in runs[1:]:
                p.remove(r)
            runs[0].find(qn("w:t")).text = "ไป ณ ที่ {สถานที่นัด}"
        elif DEFAULT_REASON in t and t.startswith("ฉะนั้น"):
            runs = p.findall(qn("w:r"))
            runs[-1].find(qn("w:t")).text = "{เหตุที่เรียก}"
    issue_label = next(p for p in paragraphs if text(p).strip() == "สถานที่ออกหมาย")
    tc = issue_label.getparent()
    nxt = tc.getnext()
    set_text(nxt.find(qn("w:p")), "{สถานที่ออกหมาย}")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def build_envelope() -> bytes:
    """ใบจ่าหน้าซองขนาด DL (220 × 110 มม.) ใช้ตัวแทนแบบเดียวกับหมายเรียก."""
    from docx.enum.section import WD_ORIENT
    from docx.shared import Mm, Pt

    from documents.management.commands.build_doc_templates import thai_font

    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Mm(220), Mm(110)
    sec.left_margin = sec.right_margin = Mm(12)
    sec.top_margin = sec.bottom_margin = Mm(10)

    def line(text, size=14, bold=False, indent=0, align=None):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        if indent:
            p.paragraph_format.left_indent = Mm(indent)
        if align is not None:
            p.alignment = align
        thai_font(p.add_run(text), size, bold)

    line("{ชื่อหน่วย}", 14, True)
    line("{ที่อยู่หน่วย}", 13)
    line("ราชการ ไม่ต้องติดแสตมป์ · หมายเรียกเลขรับแจ้ง {เลขรับแจ้ง}", 11)
    for _ in range(2):
        line("")
    line("กรุณาส่ง", 16, True, indent=95)
    line("{ชื่อผู้แจ้ง}", 18, True, indent=105)
    line("{ที่อยู่ไม่รวมรหัส}", 16, indent=105)
    line("{รหัสไปรษณีย์}", 26, True, indent=105)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def ensure_templates() -> None:
    if SummonsTemplate.objects.exists():
        return
    original = ORIGINAL.read_bytes()
    for kind, name, version, data, active in (
        (SummonsTemplate.Kind.SUMMONS, "แบบฟอร์มของหน่วย (ต้นฉบับ)", 1, original, False),
        (SummonsTemplate.Kind.SUMMONS, "แบบฟอร์มของหน่วย + ช่องวันเวลาและสถานที่นัด", 2, build_v2(original), True),
        (SummonsTemplate.Kind.ENVELOPE, "ใบจ่าหน้าซอง DL", 1, build_envelope(), True),
    ):
        t = SummonsTemplate(kind=kind, name=name, version=version, active=active, placeholders=engine.find_placeholders(data))
        t.file.save(f"{kind}-v{version}.docx", ContentFile(data), save=False)
        t.save()


def active_template(kind: str) -> SummonsTemplate:
    ensure_templates()
    return SummonsTemplate.objects.filter(kind=kind, active=True).order_by("-version").first()


def template_bytes(t: SummonsTemplate) -> bytes:
    with t.file.open("rb") as f:
        data = f.read()
    return data.replace(
        b">+14<", b">{\xe0\xb8\xa7\xe0\xb8\xb1\xe0\xb8\x99\xe0\xb8\x99\xe0\xb8\xb1\xe0\xb8\x94}<"
    )  # v1: "+14" = วันนัด


# ---------------------------------------------------------------- values and checks
def default_appointment(issued_on: date, index: int = 0, stagger: bool = False) -> datetime:
    """ออกหมาย + 14 วัน เวลา 09.00 น.; ตรงเสาร์–อาทิตย์เลื่อนเป็นวันจันทร์; ไล่เวลาทีละ 30 นาทีได้."""
    day = issued_on + timedelta(days=14)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    start = datetime.combine(day, time(9, 0)) + timedelta(minutes=30 * index if stagger else 0)
    return timezone.make_aware(start)


def address_without_postcode(c) -> str:
    pc = c.effective_postcode
    return c.address[: -len(pc)].strip() if pc and c.address.endswith(pc) else c.address


def values_for(
    rc: RegisterCase, issued_on: date, appointment: datetime, place: str, reason: str, number: int, digits: bool = True
) -> dict:
    inv = rc.investigator
    unit = rc.unit or (inv.unit if inv else None)
    c = rc.complainant
    appt = timezone.localtime(appointment)
    d = tf.thai_digits if digits else str
    return {
        "วันปัจจุบัน": d(issued_on.day), "เดือนปัจจุบัน": tf.thai_month(issued_on), "ปีปัจจุบัน": tf.be_year_2(issued_on, digits),
        "ชื่อผู้แจ้ง": c.full_name if c else rc.victim_name, "ที่อยู่": c.address if c else "",
        "ที่อยู่ไม่รวมรหัส": address_without_postcode(c) if c else "", "รหัสไปรษณีย์": c.effective_postcode if c else "",
        "ยศผู้ออกหมาย": inv.rank_full if inv else "", "ชื่อผู้ออกหมาย": inv.sign_name if inv else "",
        "ตำแหน่งผู้ออกหมาย": inv.position if inv else "", "โทรศัพท์ผู้ออกหมาย": inv.phone if inv else "",
        "วันนัด": d(appt.day), "เดือนนัด": tf.thai_month(appt.date()), "ปีนัด": tf.be_year_2(appt.date(), digits),
        "เวลานัด": d(f"{appt:%H.%M}"), "สถานที่นัด": place, "เหตุที่เรียก": reason,
        "สถานที่ออกหมาย": (unit.issue_place if unit else "") or (unit.name if unit else ""),
        "ชื่อหน่วย": unit.name if unit else "", "ที่อยู่หน่วย": unit.sender_address if unit else "",
        "เลขรับแจ้ง": rc.receipt_no, "เลขคดี": rc.case_no, "ครั้งที่": d(number),
    }  # fmt: skip


M53 = [  # ป.วิ.อ. ม.53: ข้อความที่หมายเรียกต้องมี
    ("สถานที่ออกหมาย", "สถานที่ออกหมาย"), ("วันปัจจุบัน", "วันที่ออกหมาย"), ("ชื่อผู้แจ้ง", "ชื่อผู้ถูกเรียก"),
    ("ที่อยู่", "ที่อยู่ผู้ถูกเรียก"), ("เหตุที่เรียก", "เหตุที่เรียก"), ("สถานที่นัด", "สถานที่ที่ให้มา"),
    ("วันนัด", "วันที่ที่ให้มา"), ("เวลานัด", "เวลาที่ให้มา"), ("ชื่อผู้ออกหมาย", "ชื่อผู้ออกหมาย"),
    ("ตำแหน่งผู้ออกหมาย", "ตำแหน่งผู้ออกหมาย"),
]  # fmt: skip


def check(values: dict, template: SummonsTemplate) -> list[str]:
    """ปัญหาที่ทำให้ออกหมายไม่ได้: ข้อความตาม ม.53 ขาด หรือในแบบฟอร์มมีตัวแทนที่ระบบไม่รู้จัก/ไม่มีค่า."""
    placeholders = set(template.placeholders) | ({"วันนัด"} if template.version == 1 else set())
    problems = []
    for key, label in M53:
        static_in_form = key not in placeholders and key in ("สถานที่ออกหมาย", "เหตุที่เรียก", "สถานที่นัด", "เวลานัด")
        if not static_in_form and not str(values.get(key) or "").strip():
            problems.append(f"ขาด{label} (ป.วิ.อ. ม.53)")
    for name in sorted(placeholders):
        if name not in values:
            problems.append(f"แบบฟอร์มมีตัวแทน {{{name}}} ที่ระบบไม่รู้จัก")
        elif not str(values[name] or "").strip() and name not in dict(M53):
            problems.append(f"ไม่มีค่าสำหรับ {{{name}}}")
    return problems


# ---------------------------------------------------------------- plan (before confirming)
@dataclass
class Item:
    rc: RegisterCase
    number: int
    appointment: datetime
    values: dict
    problems: list = field(default_factory=list)


def plan(cases: list[RegisterCase], opts: dict) -> list[Item]:
    """One item per case, grouped by investigator (different signers), with defaults and checks."""
    tpl = active_template(SummonsTemplate.Kind.SUMMONS)
    issued_on = opts.get("issued_on") or timezone.localdate()
    cases = sorted(cases, key=lambda rc: ((rc.investigator.name if rc.investigator else "~"), rc.receipt_no))
    last = dict(
        Summons.objects.filter(register_case__in=cases)
        .values("register_case")
        .annotate(m=Max("number"))
        .values_list("register_case", "m")
    )
    items, per_inv = [], {}
    for rc in cases:
        key = rc.investigator_id
        idx = per_inv[key] = per_inv.get(key, -1) + 1
        appt = opts.get("appointment_at") or default_appointment(issued_on, idx, opts.get("stagger", False))
        if opts.get("appointment_at") and opts.get("stagger"):
            appt = appt + timedelta(minutes=30 * idx)
        unit = rc.unit or (rc.investigator.unit if rc.investigator else None)
        place = opts.get("place") or (unit.appointment_place if unit else "")
        number = (last.get(rc.pk) or 0) + 1
        values = values_for(rc, issued_on, appt, place, opts.get("reason") or DEFAULT_REASON, number, digits=False)
        items.append(Item(rc, number, appt, values, check(values, tpl)))
    return items


ENVELOPE_OPTIONAL = {"รหัสไปรษณีย์", "ที่อยู่หน่วย", "ที่อยู่ไม่รวมรหัส"}


def fill(template: SummonsTemplate, values: dict, digits: bool) -> bytes:
    """Values are plain text with Arabic digits; the engine converts every digit when the Thai-digits option is on.
    A summons must have every placeholder filled; an envelope may leave the postcode for handwriting."""
    optional = ENVELOPE_OPTIONAL if template.kind == SummonsTemplate.Kind.ENVELOPE else ()
    data, missing = engine.fill(template_bytes(template), values, thai_digits=digits, allow_empty=optional)
    if missing:
        raise ValueError("ยังมีช่องว่าง: " + ", ".join(missing))
    return data


def preview_pdf(item: Item, digits: bool = True) -> tuple[bytes, str]:
    data = fill(active_template(SummonsTemplate.Kind.SUMMONS), item.values, digits)
    if soffice_available():
        return docx_to_pdf(data), "application/pdf"
    return data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ---------------------------------------------------------------- issue
def safe_name(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in "LMN" or ch in "-_").strip()[:60] or "ไม่ทราบชื่อ"


def start_batch(user, case_ids: list[int], opts: dict) -> SummonsBatch:
    from .tasks import run_batch_task

    serial = {k: (v.isoformat() if isinstance(v, date | datetime) else v) for k, v in opts.items()}
    batch = SummonsBatch.objects.create(created_by=user, options=serial | {"case_ids": case_ids}, total=len(case_ids))
    if len(case_ids) > SYNC_LIMIT:
        transaction.on_commit(lambda: run_batch_task.delay(batch.pk))
    else:
        run_batch(batch.pk)
        batch.refresh_from_db()
    return batch


def _opts(raw: dict) -> dict:
    o = dict(raw)
    if o.get("issued_on"):
        o["issued_on"] = date.fromisoformat(o["issued_on"])
    if o.get("appointment_at"):
        o["appointment_at"] = datetime.fromisoformat(o["appointment_at"])
    return o


def run_batch(batch_id: int) -> SummonsBatch:
    from deadlines.services import log_action

    batch = SummonsBatch.objects.select_related("created_by").get(pk=batch_id)
    batch.status = SummonsBatch.Status.RUNNING
    batch.save(update_fields=["status"])
    opts = _opts(batch.options)
    user = batch.created_by
    cases = list(visible(user).filter(pk__in=opts.get("case_ids", [])))
    tpl, env_tpl = active_template(SummonsTemplate.Kind.SUMMONS), active_template(SummonsTemplate.Kind.ENVELOPE)
    want_pdf = opts.get("formats", "docx") in ("pdf", "both") and soffice_available()
    errors, made, docs, envs = [], [], [], []
    issued_status = RegisterStatus.objects.get_or_create(name=ISSUED_STATUS)[0] if opts.get("set_status") else None
    try:
        for item in plan(cases, opts):
            rc = item.rc
            if not can_issue(user, rc):
                errors.append(f"{rc.receipt_no}: ไม่มีสิทธิ์ออกหมายในคดีนี้")
                continue
            if item.problems:
                errors.append(f"{rc.receipt_no}: " + "; ".join(item.problems))
                continue
            digits = opts.get("thai_digits", True)
            data = fill(tpl, item.values, digits)
            base = f"{rc.receipt_no}_{safe_name(item.values['ชื่อผู้แจ้ง'])}_หมายเรียกครั้งที่{item.number}"
            with transaction.atomic():
                inv = rc.investigator
                s = Summons(
                    register_case=rc, batch=batch, number=item.number, issued_on=opts.get("issued_on") or timezone.localdate(),
                    appointment_at=item.appointment, place=item.values["สถานที่นัด"], reason=item.values["เหตุที่เรียก"], issuer=inv,
                    issuer_snapshot={k: item.values[k] for k in ("ยศผู้ออกหมาย", "ชื่อผู้ออกหมาย", "ตำแหน่งผู้ออกหมาย", "โทรศัพท์ผู้ออกหมาย")},
                    template=tpl, created_by=user,
                )  # fmt: skip
                s.docx.save(f"{base}.docx", ContentFile(data), save=False)
                if want_pdf and not opts.get("combine"):
                    s.pdf.save(f"{base}.pdf", ContentFile(docx_to_pdf(data)), save=False)
                if opts.get("envelopes") and env_tpl:
                    env = fill(env_tpl, item.values, digits)
                    s.envelope.save(f"{base}_ซอง.docx", ContentFile(env), save=False)
                    envs.append(env)
                s.save()
                if issued_status and rc.status_id != issued_status.pk:
                    rc.status = issued_status
                    rc.save(update_fields=["status"])
                if rc.case_id:
                    when = timezone.localtime(item.appointment)
                    log_action(rc.case, user, f"ออกหมายเรียกผู้กล่าวหาครั้งที่ {item.number} นัด {tf.thai_date(when.date(), False)} {when:%H.%M} น.",
                               reason="ป.วิ.อาญา ม.52, ม.53")  # fmt: skip
            log(user, "export", "summons", s.pk, f"ออกหมายเรียก {rc.receipt_no} ครั้งที่ {item.number}")
            made.append(s)
            docs.append((f"{base}.docx", data, s))
            batch.done_count = len(made)
            batch.save(update_fields=["done_count"])
        _bundle(batch, opts, docs, envs, want_pdf)
        batch.status = SummonsBatch.Status.DONE if made else SummonsBatch.Status.FAILED
        batch.error = "\n".join(errors) if errors else ("" if made else "ไม่มีหมายที่ออกได้")
        if opts.get("formats") in ("pdf", "both") and not soffice_available():
            batch.error = (batch.error + "\n" if batch.error else "") + "เครื่องนี้ไม่มี LibreOffice จึงได้เฉพาะไฟล์ Word"
    except Exception as e:  # noqa: BLE001 - reported on the progress page
        batch.status, batch.error = SummonsBatch.Status.FAILED, f"สร้างหมายไม่สำเร็จ: {e}"
    batch.finished_at = timezone.now()
    batch.save()
    return batch


def combine(parts: list[bytes]) -> bytes:
    """Join .docx files with a page break between people (docxcompose)."""
    from docx.enum.text import WD_BREAK
    from docxcompose.composer import Composer

    master = Document(io.BytesIO(parts[0]))
    composer = Composer(master)
    for data in parts[1:]:
        master.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        composer.append(Document(io.BytesIO(data)))
    out = io.BytesIO()
    composer.save(out)
    return out.getvalue()


def _bundle(batch, opts, docs, envs, want_pdf) -> None:
    if not docs:
        return
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    if opts.get("combine"):
        merged = combine([d for _, d, _ in docs])
        batch.combined_docx.save(f"หมายเรียก-รวม-{stamp}.docx", ContentFile(merged), save=False)
        if want_pdf:
            batch.combined_pdf.save(f"หมายเรียก-รวม-{stamp}.pdf", ContentFile(docx_to_pdf(merged)), save=False)
    if envs:
        merged_env = combine(envs)
        batch.envelopes.save(f"ซอง-รวม-{stamp}.docx", ContentFile(merged_env), save=False)
    if not opts.get("combine") and len(docs) > 1:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data, s in docs:
                if opts.get("formats", "docx") in ("docx", "both") or not s.pdf:
                    z.writestr(name, data)
                if s.pdf:
                    with s.pdf.open("rb") as f:
                        z.writestr(name.replace(".docx", ".pdf"), f.read())
                if s.envelope:
                    with s.envelope.open("rb") as f:
                        z.writestr(s.envelope.name.rsplit("/", 1)[-1], f.read())
        batch.bundle.save(f"หมายเรียก-{stamp}.zip", ContentFile(buf.getvalue()), save=False)


# ---------------------------------------------------------------- dashboard
def dashboard(user) -> dict:
    qs = visible(user)
    open_qs = qs.exclude(status__is_final=True)
    now = timezone.now()
    summoned = Summons.objects.filter(register_case__in=qs).exclude(result=Summons.Result.CANCELLED)
    counts = {
        "total": qs.count(),
        "open": open_qs.count(),
        "never": open_qs.filter(summons__isnull=True).count(),
        "bad_address": open_qs.filter(
            Q(complainant__isnull=True) | Q(complainant__house_no="") | Q(complainant__province="")
        ).count(),
        "waiting": summoned.filter(result=Summons.Result.ISSUED, appointment_at__gte=now).count(),
        "absent": summoned.filter(result=Summons.Result.ABSENT).count(),
    }
    by_inv = (
        open_qs.values("investigator__name").order_by("investigator__name")
        .annotate(n=models_count("id"), never=models_count("id", filter=Q(summons__isnull=True)))
    )  # fmt: skip
    return {"counts": counts, "by_investigator": list(by_inv)}


def models_count(*a, **k):
    from django.db.models import Count

    return Count(*a, distinct=True, **k)


def update_address(user, c, data: dict) -> None:
    from . import postcodes
    from .importer import complainant_problems

    for k, v in data.items():
        setattr(c, k, v)
    c.postcode_filled = "" if c.postcode else postcodes.lookup(c.tambon, c.amphoe, c.province)
    c.issues = complainant_problems({f: getattr(c, f) for f in ("house_no", "province", "tambon", "amphoe", "citizen_id")})
    c.save()
    log(user, "edit", "complainant", c.pk, f"แก้ที่อยู่ผู้แจ้ง เลขรับแจ้ง {c.receipt_no}")


def strip_html_like(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
