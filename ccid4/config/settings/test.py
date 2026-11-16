from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env

SECRET_KEY = "test-only"
DEBUG = False
DEMO_MODE = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast tests only
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
MEDIA_ROOT = BASE_DIR / "data" / "test-media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
AI_BACKEND = "fake"
ASR_BACKEND = "fake"
DATABASES = {"default": env.db("DATABASE_URL", default="postgres://ccid4:ccid4@localhost:5432/ccid4")}

WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
