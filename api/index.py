"""Vercel entry point: serves the Django app in ccid4/ in preview mode (demo data only). See ccid4/docs/VERCEL_PREVIEW.md."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ccid4"
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.vercel")

SETUP_HELP = """<!doctype html><html lang="th"><meta charset="utf-8"><title>CCID4 · ตั้งค่า Vercel</title>
<body style="font-family:sans-serif;max-width:680px;margin:3rem auto;line-height:1.7;padding:0 1rem">
<h1>CCID4 ยังตั้งค่าไม่ครบ</h1><p>{error}</p>
<ol><li>Vercel → Storage → เพิ่มฐานข้อมูล Postgres (Neon) และเชื่อมกับโปรเจกต์นี้</li>
<li>Settings → Environment Variables → เพิ่ม <code>DJANGO_SECRET_KEY</code> (สุ่มอย่างน้อย 50 ตัวอักษร)</li>
<li>กด Redeploy</li></ol><p>รายละเอียด: ccid4/docs/VERCEL_PREVIEW.md</p></body></html>"""


def _setup_error_app(error: str):
    body = SETUP_HELP.format(error=error).encode()

    def app(environ, start_response):
        start_response(
            "503 Service Unavailable", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body)))]
        )
        return [body]

    return app


try:
    from django.core.exceptions import ImproperlyConfigured
    from django.core.wsgi import get_wsgi_application

    app = get_wsgi_application()
    from core.preview import ensure_ready

    ensure_ready()
except ImproperlyConfigured as e:  # missing secret key or database: show how to fix it
    app = _setup_error_app(str(e))
