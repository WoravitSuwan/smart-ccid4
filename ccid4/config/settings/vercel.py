"""Preview on Vercel: demo data only, so the team can look at the screens before installing on the unit's servers.

Never put real case data here: Vercel and the database provider are outside the unit's network.
Needs DJANGO_SECRET_KEY and a PostgreSQL URL with the pgvector and pg_trgm extensions (e.g. Neon from the Vercel
Marketplace, which sets DATABASE_URL / POSTGRES_URL). See docs/VERCEL_PREVIEW.md.
"""

import os
import tempfile

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import SECRET_KEY

if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise ImproperlyConfigured("ตั้ง DJANGO_SECRET_KEY ใน Vercel (อย่างน้อย 32 ตัวอักษร)")

DB_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_PRISMA_URL")
if not DB_URL:
    raise ImproperlyConfigured("ยังไม่ได้เชื่อมฐานข้อมูล PostgreSQL (DATABASE_URL)")
DATABASES = {"default": dj_database_url.parse(DB_URL, conn_max_age=0, ssl_require=True)}

DEBUG = False
DEMO_MODE = True  # always: preview holds fake data only
ALLOWED_HOSTS = [".vercel.app", "localhost", "127.0.0.1"] + [
    h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h
]
CSRF_TRUSTED_ORIGINS = ["https://*.vercel.app"] + [o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# Static files straight from the source folders (no collectstatic step on Vercel).
WHITENOISE_USE_FINDERS = True
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# Only /tmp is writable; generated documents disappear when the function is recycled.
MEDIA_ROOT = os.path.join(tempfile.gettempdir(), "ccid4-media")
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# No Redis or worker on Vercel: background jobs run inside the request. No GPU: AI uses the fake backend.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = None
AI_BACKEND = "fake"
ASR_BACKEND = "fake"
VERCEL_PREVIEW = True
