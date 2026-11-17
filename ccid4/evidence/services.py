"""พยานหลักฐานดิจิทัลและของกลาง (B6): รับเข้าแบบเขียนครั้งเดียว · chain of custody · ตรวจความครบถ้วน."""

from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from django.urls import reverse

from audit.services import log
from core.notify import notify

from .models import CustodyEvent, EvidenceFile, Exhibit

MAX_BYTES = getattr(settings, "EVIDENCE_MAX_MB", 2048) * 1024 * 1024
PREVIEW = {"image": ("image/jpeg", "image/png", "image/gif", "image/webp"), "pdf": ("application/pdf",),
           "audio": ("audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/x-wav", "audio/webm", "audio/aac", "audio/flac"),
           "video": ("video/mp4", "video/webm", "video/ogg", "video/quicktime")}  # fmt: skip


def kind_of(content_type: str, name: str) -> str:
    ct = (content_type or mimetypes.guess_type(name)[0] or "").lower()
    if ct.startswith("image/"):
        return "image"
    if ct == "application/pdf":
        return "pdf"
    if ct.startswith("audio/"):
        return "audio"
    if ct.startswith("video/"):
        return "video"
    if ct in ("application/msword", "text/plain", "text/csv") or "officedocument" in ct or "spreadsheet" in ct:
        return "document"
    return "other"


def can_preview(ev: EvidenceFile) -> bool:
    return ev.content_type in PREVIEW.get(ev.kind, ())


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def event(case, user, action, reason, evidence=None, exhibit=None, to_whom="", ok=None, note="") -> CustodyEvent:
    e = CustodyEvent.objects.create(case=case, evidence=evidence, exhibit=exhibit, action=action, by=user, reason=reason[:300],
                                    to_whom=to_whom[:200], ok=ok, note=note[:300])  # fmt: skip
    what = evidence.title if evidence else (exhibit.name if exhibit else "")
    log(user, {"view": "view", "download": "download"}.get(action, "edit"), "evidence" if evidence else "exhibit",
        (evidence or exhibit).pk if (evidence or exhibit) else "", f"{e.get_action_display()} {what} คดี {case}: {reason}")  # fmt: skip
    return e


# ---------------------------------------------------------------- files
@transaction.atomic
def receive(case, user, upload, title="", source="", collected_at=None, is_digital=True, exhibit=None, note="") -> EvidenceFile:
    """Store once, hash while writing, then make the file read-only on disk."""
    if upload.size > MAX_BYTES:
        raise ValidationError(f"ไฟล์ใหญ่เกิน {MAX_BYTES // (1024 * 1024)} MB")
    h = hashlib.sha256()
    for chunk in upload.chunks():
        h.update(chunk)
    upload.seek(0)
    ct = upload.content_type or mimetypes.guess_type(upload.name)[0] or "application/octet-stream"
    ev = EvidenceFile(case=case, exhibit=exhibit, title=(title or Path(upload.name).stem)[:300], kind=kind_of(ct, upload.name),
                      original_name=upload.name[:300], content_type=ct[:100], size=upload.size, sha256=h.hexdigest(), source=source[:300],
                      collected_at=collected_at, is_digital=is_digital, note=note[:300], uploaded_by=user)  # fmt: skip
    ev.file.save(upload.name, upload, save=False)
    if sha256_of(ev.file.path) != ev.sha256:
        ev.file.delete(save=False)
        raise ValidationError("ไฟล์เสียหายระหว่างรับเข้า ลองอัปโหลดใหม่")
    ev.save()
    os.chmod(ev.file.path, 0o444)
    event(case, user, CustodyEvent.Action.RECEIVE, f"รับเข้าระบบ SHA-256 {ev.sha256[:16]}…", evidence=ev, note=source)
    return ev


def register_stored(
    case, user, name: str, sha256: str, size: int, title: str, content_type: str, source: str, origin: str
) -> EvidenceFile:
    """A file another module already locked (e.g. the interview recording): listed in the register without a second copy."""
    existing = EvidenceFile.objects.filter(case=case, sha256=sha256, origin=origin).first()
    if existing:
        return existing
    ev = EvidenceFile(case=case, title=title[:300], kind=kind_of(content_type, name), original_name=Path(name).name[:300],
                      content_type=content_type[:100], size=size, sha256=sha256, source=source[:300], is_digital=True, origin=origin,
                      uploaded_by=user)  # fmt: skip
    ev.file.name = name
    ev.save()
    event(case, user, CustodyEvent.Action.RECEIVE, f"ลงทะเบียนจาก{source} SHA-256 {sha256[:16]}…", evidence=ev)
    return ev


def verify(ev: EvidenceFile, user) -> bool:
    ok = default_storage.exists(ev.file.name) and sha256_of(ev.file.path) == ev.sha256
    event(ev.case, user, CustodyEvent.Action.VERIFY, "ตรวจค่า SHA-256 เทียบกับตอนรับเข้า", evidence=ev, ok=ok,
          note="ตรงกัน" if ok else "ไม่ตรงหรือไม่พบไฟล์")  # fmt: skip
    if not ok:
        from accounts.models import User

        heads = User.objects.filter(role__in=("supervisor", "admin"), is_active=True)
        notify([ev.case.owner, *heads], "evidence_integrity", f"ค่า SHA-256 ของพยานหลักฐานไม่ตรง: {ev.title}",
               f"คดี {ev.case.label} · ตรวจโดย {user.display_name}", reverse("evidence:case", args=[ev.case_id]))  # fmt: skip
    return ok


def withdraw(ev: EvidenceFile, user, reason: str) -> None:
    if len((reason or "").strip()) < 10:
        raise ValidationError("ระบุเหตุผลอย่างน้อย 10 ตัวอักษร")
    from django.utils import timezone

    ev.withdrawn_at, ev.withdrawn_reason = timezone.now(), reason.strip()[:300]
    ev.save(update_fields=["withdrawn_at", "withdrawn_reason"])
    event(ev.case, user, CustodyEvent.Action.WITHDRAW, reason, evidence=ev)


# ---------------------------------------------------------------- exhibits
def add_exhibit(case, user, **data) -> Exhibit:
    order = (case.exhibits.aggregate(m=Max("order"))["m"] or 0) + 1
    x = Exhibit.objects.create(case=case, order=order, created_by=user, **data)
    event(case, user, CustodyEvent.Action.RECEIVE, "ตรวจยึด/รับเป็นของกลาง", exhibit=x, note=x.source)
    return x


def update_exhibit(x: Exhibit, user, data: dict, reason: str) -> list[str]:
    tracked = ("status", "storage", "holder", "condition")
    changed = [Exhibit._meta.get_field(k).verbose_name for k in tracked if k in data and data[k] != getattr(x, k)]
    if changed and len((reason or "").strip()) < 5:
        raise ValidationError(f"เปลี่ยน{' '.join(changed)} ต้องระบุเหตุผล")  # checked before anything is changed
    for k, v in data.items():
        setattr(x, k, v)
    x.save()
    if changed:
        event(x.case, user, CustodyEvent.Action.MOVE, reason, exhibit=x, note=f"เปลี่ยน{', '.join(changed)}")
    else:
        log(user, "edit", "exhibit", x.pk, f"แก้รายละเอียดของกลาง {x.name} คดี {x.case}")
    return changed


def handover(x: Exhibit, user, to_whom: str, reason: str, status: str = "") -> None:
    if not to_whom.strip() or len(reason.strip()) < 5:
        raise ValidationError("ระบุผู้รับมอบและเหตุผล")
    x.holder = to_whom.strip()[:200]
    if status in Exhibit.Status.values:
        x.status = status
    x.save(update_fields=["holder", "status", "updated_at"])
    event(x.case, user, CustodyEvent.Action.HANDOVER, reason, exhibit=x, to_whom=to_whom)


def take_back(x: Exhibit, user, from_whom: str, reason: str) -> None:
    if len(reason.strip()) < 5:
        raise ValidationError("ระบุเหตุผล")
    x.holder, x.status = "", Exhibit.Status.HELD
    x.save(update_fields=["holder", "status", "updated_at"])
    event(x.case, user, CustodyEvent.Action.RETURN, reason, exhibit=x, to_whom=from_whom)
