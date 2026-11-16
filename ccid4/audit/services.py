import hashlib
import json

from django.db import connection, transaction
from django.utils import timezone

from .context import current_request
from .models import AuditLog

_LOCK_KEY = 4_242_001  # Postgres advisory lock that serializes the hash chain


def _digest(e: AuditLog) -> str:
    payload = json.dumps(
        {"at": e.at.isoformat(), "user": e.user_id, "label": e.user_label, "action": e.action,
         "type": e.object_type, "id": e.object_id, "detail": e.detail, "ip": e.ip, "path": e.path, "prev": e.prev_hash},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _client_ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR")


def log(user, action: str, object_type: str = "", object_id="", detail: str = "") -> AuditLog:
    request = current_request.get()
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", [_LOCK_KEY])
        last = AuditLog.objects.order_by("-id").only("hash").first()
        e = AuditLog(
            at=timezone.now().replace(microsecond=0), user=user,
            user_label=user.display_name if user else "", action=action, object_type=object_type,
            object_id=str(object_id or ""), detail=detail[:4000], ip=_client_ip(request),
            path=(request.path[:300] if request is not None else ""), prev_hash=last.hash if last else "",
        )
        e.hash = _digest(e)
        e.save(force_insert=True)
    return e


def verify_chain() -> tuple[bool, int | None]:
    prev = ""
    for e in AuditLog.objects.order_by("id").iterator(chunk_size=2000):
        if e.prev_hash != prev or e.hash != _digest(e):
            return False, e.id
        prev = e.hash
    return True, None
