"""ถอดเสียงสดระหว่างบันทึก (B2).

เสียงขึ้นเซิร์ฟเวอร์ทุก 5 วินาที → งานเบื้องหลังถอดต่อจากจุดที่ค้างไว้ ทีละช่วง 8–28 วินาที ตัดตรงจังหวะหยุดพูด
(ไม่ตัดกลางคำ และโมเดลได้บริบทพอ) ด้วยโมเดลและค่าตั้งเดียวกับการถอดทั้งไฟล์ ข้อความขึ้นบนหน้าบันทึกเสียงเป็นช่วง ๆ
เมื่อปิดการบันทึก ขั้นประมวลผลใช้ข้อความที่ถอดสดไว้แล้ว และถอดเพิ่มเฉพาะส่วนท้ายที่ยังไม่ได้ถอด

เวลาใน LiveSegment นับจากต้นรอบการอัด (take) — แต่ละรอบเป็นไฟล์ WebM ของตัวเอง และต่อกันเป็นไฟล์ต้นฉบับตอนปิดการบันทึก
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ai import asr

from .models import InterviewSession, LiveSegment

logger = logging.getLogger(__name__)
LEASE = timedelta(minutes=10)  # a crashed job frees the session after this
OPEN = (InterviewSession.Status.DRAFT, InterviewSession.Status.RECORDING)


def enabled() -> bool:
    return bool(settings.ASR_LIVE) and asr.available()


def queue(session, flush: bool = False) -> None:
    """Ask the worker to transcribe what has arrived. flush = the officer paused/stopped: do the tail too."""
    if not enabled():
        return
    from .tasks import live_task

    if flush:
        transaction.on_commit(lambda: live_task.delay(session.pk, True))
    elif InterviewSession.objects.filter(pk=session.pk, live_queued=False).update(live_queued=True):
        transaction.on_commit(lambda: live_task.delay(session.pk))


def take_file(session, take: int, workdir: Path) -> Path | None:
    """The take as received so far: its chunks in order, up to the first one still on its way."""
    path, expected = workdir / f"take{take}.webm", 1
    with open(path, "wb") as out:
        for c in session.chunks.filter(take=take).order_by("seq"):
            if c.seq != expected:
                break
            with c.file.open("rb") as f:
                shutil.copyfileobj(f, out)
            expected += 1
    return path if expected > 1 else None


def run(session_id: int, flush: bool = False) -> str:
    """One pass over everything that has arrived. Returns ok · busy (another pass is running) · closed · off."""
    now = timezone.now()
    lease = Q(live_lease__isnull=True) | Q(live_lease__lt=now)
    if not InterviewSession.objects.filter(lease, pk=session_id).update(live_lease=now + LEASE, live_queued=False):
        return "busy"
    try:
        session = InterviewSession.objects.get(pk=session_id)
        if not enabled():
            return "off"
        if session.status not in OPEN or hasattr(session, "recording"):
            return "closed"
        takes = sorted(set(session.chunks.values_list("take", flat=True)))
        with tempfile.TemporaryDirectory(prefix="ccid4-live-") as tmp:
            for take in takes:
                if not _take(session, take, Path(tmp), final=flush or take < takes[-1]):
                    return "closed"
        return "ok"
    except Exception as e:  # noqa: BLE001 - live text is a convenience: the full pass after closing still runs
        logger.exception("interview %s: live transcription failed", session_id)
        InterviewSession.objects.filter(pk=session_id, recording__isnull=True).update(
            live={**_state(session_id), "error": f"{type(e).__name__}: {e}"[:200]}
        )
        return "error"
    finally:
        InterviewSession.objects.filter(pk=session_id).update(live_lease=None)


def _state(session_id: int) -> dict:
    return InterviewSession.objects.filter(pk=session_id).values_list("live", flat=True).first() or {}


def _take(session, take: int, workdir: Path, final: bool) -> bool:
    state = (session.live.get("takes") or {}).get(str(take)) or {}
    if state.get("complete"):
        return True
    done = float(state.get("done", 0.0))
    path = take_file(session, take, workdir)
    if path is None:
        return True
    audio = asr.decode_from(path, done)
    received = done + len(audio) / asr.SAMPLE_RATE
    pos = 0
    for cut in asr.pause_cuts(audio, final=final):
        items = asr.transcribe_audio(audio[pos:cut], offset=done + pos / asr.SAMPLE_RATE)
        if not _save(session, take, items, done + cut / asr.SAMPLE_RATE, received):
            return False
        pos = cut
    # an earlier take is over: once all of it is done it never needs decoding again
    complete = final and pos >= len(audio) and take < max(session.chunks.values_list("take", flat=True))
    if pos == 0 or complete:  # also report how much has arrived when nothing was ready to transcribe
        return _save(session, take, [], done + pos / asr.SAMPLE_RATE, received, complete=complete)
    return True


def _save(session, take: int, items: list[dict], done: float, received: float, complete: bool = False) -> bool:
    """Store new text and progress, unless the recording was closed meanwhile (then the full pass takes over)."""
    with transaction.atomic():
        locked = InterviewSession.objects.select_for_update().get(pk=session.pk)
        if locked.status not in OPEN or hasattr(locked, "recording"):
            return False
        LiveSegment.objects.bulk_create(
            LiveSegment(session=locked, take=take, start=d["start"], end=d["end"], text=d["text"], words=d.get("words") or [],
                        confidence=_confidence(d))  # fmt: skip
            for d in items
        )
        live = dict(locked.live or {})
        takes = dict(live.get("takes") or {})
        takes[str(take)] = {"done": round(done, 3), "received": round(received, 3), "complete": complete}
        live.update(takes=takes, updated=timezone.now().isoformat(timespec="seconds"), error="")
        locked.live = live
        locked.save(update_fields=["live"])
        session.live = live
    return True


def _confidence(item: dict) -> float | None:
    words = item.get("words") or []
    return round(sum(w.get("p", 1) for w in words) / len(words), 3) if words else None


# ---------------------------------------------------------------- for the page and for the pass after closing
def payload(session, after: int = 0) -> dict:
    takes = (session.live or {}).get("takes") or {}
    done = sum(float(t.get("done", 0)) for t in takes.values())
    received = sum(float(t.get("received", 0)) for t in takes.values())
    many = len(takes) > 1
    rows = [
        {"id": s.pk, "take": s.take if many else None, "start": round(s.start, 1), "text": s.text, "p": s.confidence}
        for s in session.live_segments.filter(pk__gt=after)
    ]
    return {"enabled": enabled(), "segments": rows, "done": round(done, 1), "received": round(received, 1),
            "busy": bool(session.live_lease), "error": (session.live or {}).get("error", "")}  # fmt: skip


def collect(session, lengths: dict[int, float]) -> tuple[list[dict], float]:
    """Live text on the timeline of the closed recording, and how many seconds from the start it already covers.

    lengths = seconds of each take in the closed recording (takes are joined in order).
    """
    takes = (session.live or {}).get("takes") or {}
    items, offset, covered = [], 0.0, 0.0
    for take in sorted(lengths):
        done = float((takes.get(str(take)) or {}).get("done", 0.0))
        for s in session.live_segments.filter(take=take):
            if s.start >= done:
                continue
            words = [{**w, "s": round(w["s"] + offset, 2), "e": round(w["e"] + offset, 2)} for w in s.words or []]
            items.append({"start": offset + s.start, "end": offset + s.end, "text": s.text, "words": words})
        if done < lengths[take] - 0.25:  # this take was not finished live: everything after it is done in full
            return items, offset + done
        offset += lengths[take]
        covered = offset
    return items, covered


def clear(session) -> None:
    session.live_segments.all().delete()
    InterviewSession.objects.filter(pk=session.pk).update(live={})
    session.live = {}
