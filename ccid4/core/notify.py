"""ส่งการแจ้งเตือนในระบบ (ไม่ส่งออกนอกเครือข่าย)."""

from __future__ import annotations

from .models import Notification


def notify(users, kind: str, title: str, body: str = "", url: str = "") -> int:
    rows = [Notification(user=u, kind=kind, title=title[:200], body=body[:500], url=url[:300]) for u in {u for u in users if u}]
    Notification.objects.bulk_create(rows)
    return len(rows)


def unread_count(user) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).count()
