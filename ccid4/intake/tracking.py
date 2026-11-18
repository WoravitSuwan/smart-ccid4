"""หน้าติดตามสถานะสำหรับผู้แจ้ง (B8)."""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.services import log
from cases.models import CaseStatus

from .models import IntakeSubmission, TrackingLink

DEFAULT_DAYS = 365


def current(case) -> TrackingLink | None:
    return next((t for t in case.tracking_links.all() if t.active), None)


def create(case, user, days: int = DEFAULT_DAYS, token: str = "", reference: str = "") -> TrackingLink:
    if not 1 <= int(days) <= 730:
        raise ValidationError("อายุลิงก์ 1–730 วัน")
    for old in case.tracking_links.filter(revoked_at__isnull=True):  # one live link per case
        old.revoked_at = timezone.now()
        old.save(update_fields=["revoked_at"])
    t = TrackingLink.objects.create(case=case, expires_at=timezone.now() + timedelta(days=int(days)), created_by=user,
                                    reference=reference or case.receipt_no or case.case_no or f"คดี {case.pk}",
                                    **({"token": token} if token else {}))  # fmt: skip
    if user:
        log(user, "create", "tracking_link", t.pk, f"สร้างลิงก์ติดตามให้ผู้แจ้ง คดี {case} อายุ {days} วัน")
    return t


def revoke(t: TrackingLink, user) -> None:
    t.revoked_at = timezone.now()
    t.save(update_fields=["revoked_at"])
    log(user, "edit", "tracking_link", t.pk, f"ยกเลิกลิงก์ติดตามของผู้แจ้ง คดี {t.case}")


def publish_message(case, user, text: str):
    """ข้อความแจ้งความคืบหน้าที่ผู้แจ้งเห็น: เก็บเป็นรายการในบันทึกพนักงานสอบสวน (ติ๊กให้ผู้แจ้งเห็นแล้ว)."""
    from deadlines.models import Task

    text = (text or "").strip()
    if len(text) < 5:
        raise ValidationError("พิมพ์ข้อความอย่างน้อย 5 ตัวอักษร")
    t = Task.objects.create(case=case, title=text[:300], reason="ข้อความแจ้งความคืบหน้าถึงผู้แจ้ง", source=Task.Source.AUTO,
                            done_at=timezone.now(), done_by=user, created_by=user, show_to_complainant=True)  # fmt: skip
    log(user, "edit", "tracking_link", case.pk, f"เผยแพร่ข้อความถึงผู้แจ้ง คดี {case}")
    return t


def steps(case, started) -> list[tuple[str, str]]:
    """Only the status and the items the investigator published — nothing else from the case."""
    out = [("รับเรื่องแล้ว", timezone.localtime(started).strftime("%d/%m/%Y %H:%M"))]
    out.append(("พนักงานสอบสวนรับเป็นคดีแล้ว", ""))
    for t in case.tasks.filter(show_to_complainant=True, done_at__isnull=False).order_by("done_at"):
        out.append((t.title, timezone.localtime(t.done_at).strftime("%d/%m/%Y")))
    if case.status == CaseStatus.CLOSED:
        out.append(("การสอบสวนเสร็จสิ้น", ""))
    return out


def resolve(token: str):
    """(link or submission, case or None, state) where state is ok | expired | missing."""
    t = TrackingLink.objects.select_related("case").filter(token=token).first()
    if t:
        return t, t.case, ("ok" if t.active else "expired")
    sub = IntakeSubmission.objects.select_related("case").filter(track_token=token).first()
    if sub:  # a submission whose case was not created yet (or a link made before tracking links existed)
        return sub, sub.case, "ok"
    return None, None, "missing"
