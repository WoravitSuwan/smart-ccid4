from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import SECRET_KEY, env

if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise ImproperlyConfigured("ตั้ง DJANGO_SECRET_KEY ใน .env (อย่างน้อย 32 ตัวอักษร)")

DEBUG = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env.bool("COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_CONTENT_TYPE_NOSNIFF = True
