"""การแจ้งเตือนกำหนดเวลา (B8): เหลือ 3 วัน · 1 วัน · ครบวันนี้ · เลยกำหนด — แจ้งในกระดิ่งครั้งเดียวต่อขั้น · สรุปรายวันทางอีเมล."""

from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from cases.models import Case, CaseStatus
from core import thai
from core.notify import notify

from .models import DeadlineAlert, DigestSetting
from .services import deadline_rows, personal

STAGE_OF = {3: DeadlineAlert.Stage.D3, 1: DeadlineAlert.Stage.D1, 0: DeadlineAlert.Stage.D0}


def stage_for(days_left: int) -> str | None:
    return DeadlineAlert.Stage.OVER if days_left < 0 else STAGE_OF.get(days_left)


def deadline_key(it) -> str:
    d = it.deadline
    due = d.due.isoformat() if hasattr(d.due, "isoformat") else str(d.due)
    return f"{d.key}:{d.party_id or ''}:{due}"[:120]


def run_alerts(now=None) -> int:
    """Hourly (Celery beat). Returns how many new alerts were sent."""
    cases = (
        Case.objects.alive()
        .exclude(status=CaseStatus.CLOSED)
        .select_related("owner")
        .prefetch_related("parties__person", "parties__detentions", "tasks", "shared_with")
    )
    sent = 0
    for case, it in deadline_rows(cases, now):
        stage = stage_for(it.status.days_left)
        if not stage:
            continue
        _, created = DeadlineAlert.objects.get_or_create(case=case, key=deadline_key(it), stage=stage)
        if not created:
            continue
        label = DeadlineAlert.Stage(stage).label
        notify([case.owner, *case.shared_with.all()], "deadline", f"{label}: คดี {case.label} · {it.deadline.short or it.deadline.label}",
               f"{it.status.text} · {it.deadline.ref}".strip(" ·"), reverse("cases:detail", args=[case.pk]))  # fmt: skip
        sent += 1
    return sent


def digest_text(user) -> tuple[str, str] | None:
    """Counts and case numbers only (no names or ID numbers) — the mail leaves the app, even if only to the unit's server."""
    data = personal(user)
    if not (data["overdue"] or data["week"] or data["todo_count"] or data["review_count"]):
        return None
    lines = [f"สรุปงานประจำวัน {thai.format_date(timezone.localdate(), 'long')} · {user.display_name}", ""]
    lines.append(f"เลยกำหนด {len(data['overdue'])} รายการ")
    lines += [f"  - คดี {c.label}: {it.deadline.short or it.deadline.label} ({it.status.text})" for c, it in data["overdue"][:20]]
    lines.append(f"กำหนดเวลาใน 7 วัน {len(data['week'])} รายการ")
    lines += [f"  - คดี {c.label}: {it.deadline.short or it.deadline.label} ({it.status.text})" for c, it in data["week"][:20]]
    lines.append(f"งานค้าง {len(data['tasks'])} รายการ · ร่างจาก AI รอตรวจ {data['review_count']} รายการ")
    lines += [
        "",
        f"เปิดระบบ: {getattr(settings, 'CCID4_SITE_URL', '') or 'CCID4 ของหน่วย'}",
        "อีเมลนี้ส่งจากระบบ CCID4 ภายในหน่วย ห้ามส่งต่อ",
    ]
    return f"CCID4 สรุปงาน: เลยกำหนด {len(data['overdue'])} · ใน 7 วัน {len(data['week'])}", "\n".join(lines)


def send_digests() -> int:
    """Every morning (Celery beat), only when the unit switched it on (CCID4_DAILY_DIGEST) and the user opted in."""
    if not getattr(settings, "DAILY_DIGEST", False):
        return 0
    today = timezone.localdate()
    n = 0
    for s in DigestSetting.objects.filter(enabled=True).exclude(email="").exclude(last_sent=today).select_related("user"):
        if not s.user.is_active:
            continue
        msg = digest_text(s.user)
        if msg:
            send_mail(msg[0], msg[1], settings.DEFAULT_FROM_EMAIL, [s.email], fail_silently=False)
            n += 1
        s.last_sent = today
        s.save(update_fields=["last_sent"])
    return n
