"""รับแจ้งความออนไลน์และนำเข้าคดี (A2).

The public side only calls create_submission (insert-only). process_submission runs inside the unit's network
(Celery) and turns a submission into a case owned by the link's investigator.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from ai import llm
from ai.ocr import read as ocr_read
from audit.services import log
from cases.models import Case, CaseStatus, CaseType
from parties.models import CaseParty, Person, PropertyItem

from . import interview
from .models import ArrestImport, IntakeFile, IntakeKind, IntakeLink, IntakeSubmission

ALLOWED_TYPES = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic", "image/heif": ".heif",
    "application/pdf": ".pdf",
}  # fmt: skip
MAGIC = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png", b"%PDF": "application/pdf"}


# ---------------------------------------------------------------- links
def link_for(user) -> IntakeLink:
    link = IntakeLink.objects.filter(owner=user, active=True).first()
    return link or IntakeLink.objects.create(owner=user)


@transaction.atomic
def regenerate_link(user) -> IntakeLink:
    IntakeLink.objects.filter(owner=user, active=True).update(active=False)
    log(user, "edit", "intake_link", user.pk, "สร้างลิงก์รับแจ้งความใหม่ (ลิงก์และ QR เดิมใช้ไม่ได้แล้ว)")
    return IntakeLink.objects.create(owner=user)


def public_url(request, link: IntakeLink, kind: str = "") -> str:
    from django.urls import reverse

    path = reverse("intake_public:start", args=[link.token]) + (f"?kind={kind}" if kind else "")
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}{path}" if base else request.build_absolute_uri(path)


# ---------------------------------------------------------------- public-side protection
def client_ip(request) -> str:
    return (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", ""))[:64]


def allow(key: str, limit: int, seconds: int) -> bool:
    """Fixed-window rate limit in the cache. True = allowed."""
    ck = "rl:" + hashlib.sha256(key.encode()).hexdigest()[:32]
    added = cache.add(ck, 1, seconds)
    if added:
        return True
    try:
        n = cache.incr(ck)
    except ValueError:
        cache.set(ck, 1, seconds)
        return True
    return n <= limit


def sniff(data: bytes, declared: str) -> str | None:
    for magic, ctype in MAGIC.items():
        if data.startswith(magic):
            return ctype
    if data[:12].find(b"WEBP") >= 0 and data.startswith(b"RIFF"):
        return "image/webp"
    if b"ftyphei" in data[:32] or b"ftypmif1" in data[:32] or b"ftypheic" in data[:32]:
        return "image/heic"
    return None


def check_files(files) -> tuple[list[tuple[str, str, bytes]], list[str]]:
    """Return (name, content_type, bytes) for good files and Thai error messages for the rest."""
    good, errors = [], []
    limit = settings.INTAKE_MAX_FILE_MB * 1024 * 1024
    if len(files) > settings.INTAKE_MAX_FILES:
        errors.append(f"แนบได้ไม่เกิน {settings.INTAKE_MAX_FILES} ไฟล์")
        files = files[: settings.INTAKE_MAX_FILES]
    for f in files:
        if f.size > limit:
            errors.append(f"{f.name}: ใหญ่เกิน {settings.INTAKE_MAX_FILE_MB} MB")
            continue
        data = f.read()
        ctype = sniff(data, f.content_type)
        if ctype not in ALLOWED_TYPES:
            errors.append(f"{f.name}: รับเฉพาะภาพ (JPG PNG WEBP HEIC) และ PDF")
            continue
        good.append((f.name[:200], ctype, data))
    return good, errors


# ---------------------------------------------------------------- submissions
@transaction.atomic
def create_submission(link, kind, mode, answers, chat, files, staff=None) -> IntakeSubmission:
    sub = IntakeSubmission.objects.create(
        link=link, kind=kind, mode=mode, answers=answers, chat=chat, staff=staff,
        consent_text=interview.PDPA_STAFF_TEXT if staff else interview.PDPA_TEXT, consent_at=timezone.now(),
    )  # fmt: skip
    for name, ctype, data in files:
        f = IntakeFile(
            submission=sub, original_name=name, content_type=ctype, size=len(data), sha256=hashlib.sha256(data).hexdigest()
        )
        f.file.save(f"{f.sha256[:16]}{ALLOWED_TYPES[ctype]}", ContentFile(data), save=False)
        f.save()
    if staff:
        log(staff, "create", "intake", sub.pk, f"กรอกแจ้งความแทนผู้แจ้ง เลขอ้างอิง {sub.reference}")
    from .tasks import process_submission_task

    transaction.on_commit(lambda: process_submission_task.delay(sub.pk))
    return sub


def _name(d: dict | None) -> dict:
    d = d or {}
    return {
        "title": (d.get("title") or "")[:40],
        "first_name": (d.get("first_name") or "ไม่ทราบชื่อ")[:120],
        "last_name": (d.get("last_name") or "")[:120],
    }


def _person(d: dict | None, **extra) -> Person:
    d = d or {}
    cid = re.sub(r"\D", "", d.get("citizen_id") or "")
    return Person.objects.create(
        **_name(d), phone=(d.get("phone") or "")[:40], address_text=(d.get("address") or "")[:400],
        occupation=(d.get("occupation") or "")[:120], citizen_id=cid if len(cid) == 13 else "", marks=d.get("marks") or "", **extra,
    )  # fmt: skip


def _aware(value: str):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def money(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def accounts_text(accounts: list[dict]) -> str:
    lines = []
    for a in accounts or []:
        parts = [
            a.get("bank"),
            a.get("account_no") and f"เลขที่ {a['account_no']}",
            a.get("account_name"),
            a.get("amount") and f"{a['amount']:,.2f} บาท",
            a.get("at"),
        ]
        lines.append("- " + " ".join(str(p) for p in parts if p))
    return "\n".join(lines)


@transaction.atomic
def build_case(
    owner, data: dict, *, case_type: str, status: str, receipt_no: str = "", reported_at=None, extra_notes: str = ""
) -> Case:
    """Create a case and spread the extracted data over its tabs (A2.2: แยกข้อมูลลงแท็บ...)."""
    from deadlines.services import sync_rule_tasks

    notes = [extra_notes] if extra_notes else []
    if data.get("accounts"):
        notes.append("บัญชีปลายทางที่ผู้เสียหายโอนเงินไป:\n" + accounts_text(data["accounts"]))
    circumstances = "\n\n".join(x for x in [data.get("circumstances", "").strip(), *notes] if x)
    case = Case.objects.create(
        owner=owner, case_type=case_type, status=status, receipt_no=receipt_no[:40], title=(data.get("title") or "")[:300],
        offense=(data.get("offense") or "")[:400], law_sections=(data.get("law_sections") or "")[:400],
        circumstances=circumstances, incident_from=_aware(data.get("incident_at", "")), location=(data.get("location") or "")[:400],
        damage_amount=money(data.get("damage_amount")), reported_at=reported_at or timezone.now(),
    )  # fmt: skip
    if data.get("accuser"):
        CaseParty.objects.create(case=case, person=_person(data["accuser"]), role="accuser", order=1)
    for i, s in enumerate(data.get("suspects") or [], start=1):
        at = _aware(s.get("arrested_at", ""))
        CaseParty.objects.create(case=case, person=_person(s), role="suspect", order=i, arrested_at=at)
    for i, w in enumerate(data.get("witnesses") or [], start=1):
        CaseParty.objects.create(case=case, person=_person(w), role="witness", order=i)
    for p in data.get("properties") or []:
        if p.get("name"):
            PropertyItem.objects.create(case=case, name=p["name"][:300], value=money(p.get("value")))
    sync_rule_tasks(case)
    return case


def process_submission(sub: IntakeSubmission) -> IntakeSubmission:
    from deadlines.services import log_action

    if sub.status == IntakeSubmission.Status.CASE_CREATED:
        return sub
    owner = sub.link.owner
    kind_label = IntakeKind(sub.kind).label
    try:
        text = interview.transcript_text(sub.kind, sub.answers)
        data = llm.complete_json("extract_intake", text, user=owner, extra={"answers": sub.answers, "kind_label": kind_label})
    except llm.AIError as e:
        sub.status, sub.error, sub.processed_at = IntakeSubmission.Status.FAILED, str(e)[:300], timezone.now()
        sub.save()
        return sub
    with transaction.atomic():
        case = build_case(
            owner, data, case_type=CaseType.ONLINE if sub.kind == IntakeKind.ONLINE else CaseType.CRIMINAL, status=CaseStatus.RECEIVED,
            receipt_no=sub.reference, reported_at=sub.created_at,
            extra_notes=f"แนบไฟล์จากผู้แจ้ง {sub.files.count()} ไฟล์" if sub.files.exists() else "",
        )  # fmt: skip
        sub.extracted, sub.case, sub.status, sub.error, sub.processed_at = (
            data,
            case,
            IntakeSubmission.Status.CASE_CREATED,
            "",
            timezone.now(),
        )
        sub.save()
        from .tracking import create as create_tracking

        create_tracking(case, None, token=sub.track_token, reference=sub.reference)  # the link the citizen already has
        how = "เจ้าหน้าที่กรอกแทนผู้แจ้ง" if sub.staff_id else IntakeSubmission.Mode(sub.mode).label
        log(owner, "create", "case", case.pk, f"ตั้งคดีจากการแจ้งความออนไลน์ เลขอ้างอิง {sub.reference} ({how})")
        log_action(case, owner, f"รับแจ้งความออนไลน์ เลขอ้างอิง {sub.reference} ({how})", reason="ข้อมูลแยกลงแท็บโดย AI – ตรวจทานทุกช่อง")
    return sub


def track_status(sub: IntakeSubmission) -> list[tuple[str, str]]:
    """What the citizen may see (B8): only the status and progress items the investigator chose to publish."""
    steps = [("รับเรื่องแล้ว", timezone.localtime(sub.created_at).strftime("%d/%m/%Y %H:%M"))]
    if sub.case_id:
        steps.append(("พนักงานสอบสวนรับเป็นคดีแล้ว", ""))
        for t in sub.case.tasks.filter(show_to_complainant=True, done_at__isnull=False).order_by("done_at"):
            steps.append((t.title, timezone.localtime(t.done_at).strftime("%d/%m/%Y")))
        if sub.case.status == CaseStatus.CLOSED:
            steps.append(("การสอบสวนเสร็จสิ้น", ""))
    elif sub.status == IntakeSubmission.Status.REJECTED:
        steps.append(("ไม่รับเรื่อง กรุณาติดต่อสถานี", ""))
    return steps


# ---------------------------------------------------------------- import from arrest records (A2.3)
def start_import(user, upload, source: str) -> ArrestImport:
    files, errors = check_files([upload])
    if errors:
        raise llm.AIError(errors[0])
    name, ctype, data = files[0]
    imp = ArrestImport(
        source=source, original_name=name, content_type=ctype, sha256=hashlib.sha256(data).hexdigest(), created_by=user
    )
    imp.file.save(f"{imp.sha256[:16]}{ALLOWED_TYPES[ctype]}", ContentFile(data), save=False)
    imp.save()
    log(user, "import", "arrest_import", imp.pk, f"อัปโหลด{imp.get_source_display()} {name} (SHA-256 {imp.sha256[:12]}…)")
    try:
        imp.ocr_text = ocr_read(data, ctype)
        imp.extracted = llm.complete_json(
            "extract_arrest", imp.ocr_text, user=user, extra={"source_label": imp.get_source_display()}
        )
    except llm.AIError as e:
        imp.status, imp.error = ArrestImport.Status.FAILED, str(e)[:300]
    imp.save()
    return imp


def finish_import(imp: ArrestImport, user, data: dict) -> Case:
    from deadlines.services import log_action

    notes = []
    if data.get("arrest_place"):
        notes.append(f"สถานที่จับกุม: {data['arrest_place']}")
    if data.get("arrest_team"):
        notes.append("เจ้าหน้าที่ผู้จับกุม: " + ", ".join(data["arrest_team"]))
    if data.get("exhibits"):
        notes.append(f"ของกลาง: {data['exhibits']}")
    ctype = CaseType.TRAFFIC if imp.source == ArrestImport.Source.TRAFFIC else CaseType.CRIMINAL
    with transaction.atomic():
        case = build_case(user, data, case_type=ctype, status=CaseStatus.IMPORTED, extra_notes="\n".join(notes))
        imp.case, imp.status, imp.extracted = case, ArrestImport.Status.DONE, data
        imp.save()
        log(user, "create", "case", case.pk, f"สร้างคดีจาก{imp.get_source_display()} {imp.original_name}")
        log_action(case, user, f"นำเข้าคดีจาก{imp.get_source_display()} (ตรวจทานแล้ว)", reason=f"ไฟล์ต้นฉบับ SHA-256 {imp.sha256}")
    return case
