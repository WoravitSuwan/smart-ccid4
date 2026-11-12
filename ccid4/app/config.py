"""Settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
import secrets
import warnings
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


DATA_DIR = Path(os.environ.get("CCID4_DATA_DIR", BASE_DIR / "data")).resolve()
AUDIO_DIR = DATA_DIR / "audio"
OUTPUT_DIR = DATA_DIR / "output"
UPLOAD_DIR = DATA_DIR / "uploads"
for _d in (DATA_DIR, AUDIO_DIR, OUTPUT_DIR, UPLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'ccid4.db'}")

SECRET_KEY = os.environ.get("CCID4_SECRET_KEY", "")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    warnings.warn("CCID4_SECRET_KEY ไม่ได้ตั้งค่า ใช้คีย์ชั่วคราว (ผู้ใช้จะต้องเข้าสู่ระบบใหม่เมื่อรีสตาร์ต)")

# Show the "ข้อมูลจำลอง" badge; turn off in real deployments.
DEMO_MODE = _bool("CCID4_DEMO_MODE", True)
# Set true behind HTTPS so session cookies are Secure.
COOKIE_SECURE = _bool("CCID4_COOKIE_SECURE", False)
SESSION_HOURS = int(os.environ.get("CCID4_SESSION_HOURS", "8"))

UNIT_NAME = os.environ.get("CCID4_UNIT_NAME", "กองกำกับการ 1 กองบังคับการตำรวจสืบสวนสอบสวนอาชญากรรมทางเทคโนโลยี 4")
UNIT_SHORT = os.environ.get("CCID4_UNIT_SHORT", "กก.1 บก.สอท.4")

# Speech recognition (optional). Empty = disabled; transcripts can then be typed/pasted.
# Example: ASR_MODEL=large-v3 or a local path to a CTranslate2 Thai Whisper model.
ASR_MODEL = os.environ.get("CCID4_ASR_MODEL", "")
ASR_DEVICE = os.environ.get("CCID4_ASR_DEVICE", "auto")
ASR_LANGUAGE = os.environ.get("CCID4_ASR_LANGUAGE", "th")

SOFFICE_BIN = os.environ.get("CCID4_SOFFICE", "soffice")
DOC_FONT = os.environ.get("CCID4_DOC_FONT", "TH Sarabun New")

# Case statuses that count as finished (excluded from batch summons).
CLOSED_STATUSES = [s.strip() for s in os.environ.get("CCID4_CLOSED_STATUSES", "สิ้นสุด,ส่งอัยการแล้ว").split(",") if s.strip()]
