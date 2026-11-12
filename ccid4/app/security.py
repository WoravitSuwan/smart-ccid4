"""Password hashing and request authentication (signed session cookie)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

_ITER = 390_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "รหัสผ่านต้องยาวอย่างน้อย 10 ตัวอักษร"
    if password.isdigit() or password.isalpha():
        return "รหัสผ่านต้องมีทั้งตัวอักษรและตัวเลข"
    return None


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    exp = request.session.get("exp", 0)
    if not uid or exp < time.time():
        request.session.clear()
        raise LoginRequired()
    user = db.get(User, uid)
    if not user or not user.active:
        request.session.clear()
        raise LoginRequired()
    return user


def require(perm: str):
    def dep(user: User = Depends(current_user)) -> User:
        if not user.can(perm):
            raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ทำรายการนี้")
        return user
    return dep


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


async def check_csrf(request: Request) -> None:
    """Reject state-changing form posts without the session's CSRF token."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.headers.get("x-csrf-token")
    if not sent:
        ctype = request.headers.get("content-type", "")
        if "form" in ctype:
            form = await request.form()
            sent = form.get("csrf")
    if not sent or not hmac.compare_digest(str(sent), request.session.get("csrf", "")):
        raise HTTPException(status_code=403, detail="แบบฟอร์มหมดอายุ โปรดโหลดหน้าใหม่")
