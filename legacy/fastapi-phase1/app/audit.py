"""Append-only, hash-chained audit log.

Each entry stores the hash of the previous entry; editing or deleting any row
breaks the chain, which `verify_chain` detects.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditLog, User

ACTIONS = {
    "login": "เข้าสู่ระบบ", "login_failed": "เข้าสู่ระบบไม่สำเร็จ", "logout": "ออกจากระบบ",
    "view": "เปิดดู", "create": "สร้าง", "edit": "แก้ไข", "delete": "ลบ",
    "download": "ดาวน์โหลด", "listen": "ฟังเสียง", "record": "บันทึกเสียง",
    "confirm": "ยืนยัน", "import": "นำเข้า", "export": "ส่งออก",
}


def _digest(e: AuditLog) -> str:
    payload = json.dumps({
        "at": e.at.isoformat(timespec="seconds"), "user_id": e.user_id, "user_name": e.user_name,
        "action": e.action, "object_type": e.object_type, "object_id": e.object_id,
        "detail": e.detail, "ip": e.ip, "prev": e.prev_hash,
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def log(db: Session, user: User | None, action: str, object_type: str = "", object_id="",
        detail: str = "", request: Request | None = None, commit: bool = True) -> AuditLog:
    from .models import now
    last = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
    e = AuditLog(
        at=now().replace(microsecond=0),
        user_id=user.id if user else None,
        user_name=user.full_name if user else "",
        action=action, object_type=object_type, object_id=str(object_id), detail=detail,
        ip=(request.client.host if request and request.client else ""),
        prev_hash=last.hash if last else "",
    )
    e.hash = _digest(e)
    db.add(e)
    if commit:
        db.commit()
    return e


def verify_chain(db: Session) -> tuple[bool, int | None]:
    """Returns (ok, id_of_first_bad_entry)."""
    prev = ""
    for e in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars():
        if e.prev_hash != prev or e.hash != _digest(e):
            return False, e.id
        prev = e.hash
    return True, None
