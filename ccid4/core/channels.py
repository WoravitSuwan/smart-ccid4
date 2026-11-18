"""ช่องทางแจ้งเตือนภายนอก (B8) — ส่งข้อมูลออกนอกเครือข่าย จึงปิดไว้เป็นค่าเริ่มต้น.

เปิดได้เมื่อหน่วยอนุมัติแล้วเท่านั้น: CCID4_EXTERNAL_CHANNELS=webhook และ CCID4_WEBHOOK_URL=<ปลายทางที่อนุมัติ>
ส่งเฉพาะหัวข้อสั้น ๆ ไม่มีรายละเอียดคดีหรือข้อมูลบุคคล.
"""

from __future__ import annotations

import json
import logging
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


class Channel:
    name = ""

    def send(self, user, title: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class WebhookChannel(Channel):
    """POST {"user": <username>, "text": <title>} to one approved URL (e.g. the unit's own chat bridge)."""

    name = "webhook"

    def send(self, user, title: str) -> None:
        url = getattr(settings, "EXTERNAL_WEBHOOK_URL", "")
        if not url:
            return
        body = json.dumps({"user": user.username, "text": title[:120]}, ensure_ascii=False).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5).close()  # noqa: S310 - URL set by the unit's administrator


REGISTRY = {c.name: c for c in (WebhookChannel(),)}


def enabled() -> list[Channel]:
    return [REGISTRY[n] for n in getattr(settings, "EXTERNAL_CHANNELS", []) if n in REGISTRY]


def dispatch(users, title: str) -> None:
    for ch in enabled():
        for u in users:
            try:
                ch.send(u, title)
            except Exception:  # noqa: BLE001 - an outside channel must never break the app
                logger.warning("external channel %s failed", ch.name, exc_info=True)
