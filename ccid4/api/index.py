"""Vercel entry point (preview only: demo data in /tmp, reset whenever the instance restarts)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CCID4_PREVIEW", "1")

from app.cli import seed_demo  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402

init_db()
seed_demo(SessionLocal())

from app.main import app  # noqa: E402,F401
