"""Shared settings. Secrets and hosts come from the environment (.env), never from code."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "axes",
    "core",
    "accounts",
    "audit",
    "cases",
    "parties",
    "deadlines",
    "documents",
    "ai",
    "dika",
    "intake",
    "summons",
    "interviews",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "audit.middleware.AuditContextMiddleware",
    "accounts.middleware.AccountPolicyMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.ui",
            ],
            "builtins": ["core.templatetags.thai"],
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default="postgres://ccid4:ccid4@localhost:5432/ccid4")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["axes.backends.AxesStandaloneBackend", "accounts.backends.ExpiringModelBackend"]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"

# Sessions end after 30 minutes without activity.
SESSION_COOKIE_AGE = env.int("SESSION_IDLE_SECONDS", default=30 * 60)
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Lock an account after 5 wrong passwords; unlock after 30 minutes or by an administrator.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = env.float("AXES_COOLOFF_HOURS", default=0.5)
AXES_LOCKOUT_PARAMETERS = ["username"]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "accounts/locked.html"
AXES_VERBOSE = False

OTP_TOTP_ISSUER = "CCID4"

LANGUAGE_CODE = "th"
TIME_ZONE = "Asia/Bangkok"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
MEDIA_ROOT = Path(env("CCID4_DATA_DIR", default=str(BASE_DIR / "data"))) / "media"
MEDIA_URL = "/media/"  # never served directly; files go through permission-checked views

CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_EAGER", default=False)
CELERY_TIMEZONE = TIME_ZONE
# With a GPU worker (docker compose --profile gpu) send audio processing to its own queue.
CELERY_TASK_ROUTES = {"interviews.tasks.process_task": {"queue": "asr"}} if env.bool("CCID4_ASR_QUEUE", default=False) else {}
CELERY_BEAT_SCHEDULE = {
    "intake-pending": {"task": "intake.tasks.process_pending", "schedule": 60.0},
}

UNIT_NAME = env("CCID4_UNIT_NAME", default="กองกำกับการ 1 กองบังคับการตำรวจสืบสวนสอบสวนอาชญากรรมทางเทคโนโลยี 4")
UNIT_SHORT = env("CCID4_UNIT_SHORT", default="กก.1 บก.สอท.4")
DEMO_MODE = env.bool("CCID4_DEMO_MODE", default=False)
DOC_FONT = env("CCID4_DOC_FONT", default="TH SarabunIT๙")
SOFFICE_BIN = env("CCID4_SOFFICE", default="soffice")

# AI runs on the unit's own machines only (see ai/).
AI_BACKEND = env("AI_BACKEND", default="fake")  # fake | ollama
OLLAMA_URL = env("OLLAMA_URL", default="http://ollama:11434")
OLLAMA_MODEL = env("OLLAMA_MODEL", default="")
ASR_BACKEND = env("ASR_BACKEND", default="fake")  # fake | whisper
ASR_MODEL = env("ASR_MODEL", default="")  # path to a CTranslate2 Typhoon Whisper model (faster-whisper)
ASR_DEVICE = env("ASR_DEVICE", default="auto")  # cuda | cpu | auto
DIARIZATION_MODEL = env("DIARIZATION_MODEL", default="")  # local pyannote pipeline folder; empty = officer labels speakers
FFMPEG_BIN = env("CCID4_FFMPEG", default="ffmpeg")
OLLAMA_TIMEOUT = env.int("OLLAMA_TIMEOUT", default=90)
OLLAMA_EMBED_MODEL = env("OLLAMA_EMBED_MODEL", default="")  # e.g. bge-m3
AI_EMBED_DIM = 1024  # bge-m3
OCR_BACKEND = env("OCR_BACKEND", default="fake")  # fake (text layer of PDFs only) | ollama (vision model)
OCR_MODEL = env("OCR_MODEL", default="")
PUBLIC_BASE_URL = env("CCID4_PUBLIC_URL", default="")  # address printed on QR codes, e.g. https://report.unit.example
INTAKE_MAX_FILES = 5
INTAKE_MAX_FILE_MB = 10

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
