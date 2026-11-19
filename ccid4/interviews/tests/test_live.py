"""ถอดเสียงสด: ถอดระหว่างบันทึก ตัดตรงจังหวะหยุดพูด แล้วใช้ต่อเมื่อปิดการบันทึก (ไม่ถอดซ้ำ)."""

import os
import shutil
import subprocess
import wave

import numpy as np
import pytest
from django.urls import reverse
from django.utils import timezone

from ai import asr
from interviews import live, services
from interviews.models import InterviewSession, LiveSegment

from .test_interviews import suspect  # noqa: F401 - fixture

SR = asr.SAMPLE_RATE
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def voice(pattern) -> np.ndarray:
    """Speech-like test sound: [(seconds, talking?)] — a syllable-rate modulated tone, or quiet room noise."""
    rng, parts = np.random.default_rng(0), []
    for seconds, talking in pattern:
        n = int(seconds * SR)
        if talking:
            t = np.arange(n) / SR
            parts.append(0.3 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)))
        else:
            parts.append(rng.normal(0, 0.0005, n))  # about -66 dB
    return np.concatenate(parts).astype(np.float32)


def secs(cuts):
    return [round(c / SR, 1) for c in cuts]


# ---------------------------------------------------------------- where windows are cut
def test_cuts_land_in_pauses_after_enough_speech():
    audio = voice([(5, True), (1, False), (9, True), (1, False), (6, True)])  # pauses at 5–6 s and 15–16 s
    assert secs(asr.pause_cuts(audio)) == [15.5]  # 5.5 s is too short a window; the tail may still be talking
    assert secs(asr.pause_cuts(audio, final=True)) == [22.0]  # all of it fits in one window


def test_no_cut_mid_sentence_until_a_pause_or_the_limit():
    assert asr.pause_cuts(voice([(6, True)])) == []
    assert secs(asr.pause_cuts(voice([(6, True)]), final=True)) == [6.0]
    long = asr.pause_cuts(voice([(40, True)]))  # 40 s without a breath: forced cuts, each window 8–28 s
    edges = [0, *long]
    assert long and all(8 * SR <= b - a <= 28 * SR for a, b in zip(edges, edges[1:], strict=False))
    assert long[-1] <= 39 * SR


def test_speaker_silent_right_now_is_a_safe_place_to_cut():
    assert 10.0 < secs(asr.pause_cuts(voice([(10, True), (1, False)])))[0] <= 10.3


# ---------------------------------------------------------------- end to end with a browser-like recording
@pytest.fixture
def browser_chunks(tmp_path):
    """A WebM/Opus recording cut into byte pieces like MediaRecorder's 5-second blobs (only the first has the header)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")

    def make(pattern, pieces=5):
        wav = tmp_path / "talk.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((voice(pattern) * 32767).astype(np.int16).tobytes())
        data = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus", "-b:a", "64k", "-f", "webm", "pipe:1"],
                              capture_output=True, check=True).stdout  # fmt: skip
        size = -(-len(data) // pieces)
        return [data[i : i + size] for i in range(0, len(data), size)]

    return make


@pytest.fixture
def fake_model(monkeypatch):
    """The real decoding and cutting, a stand-in for the speech model."""
    calls = []

    def transcribe_audio(audio, offset=0.0, progress=None):
        calls.append((round(offset, 2), round(len(audio) / SR, 2)))
        return [{"start": offset, "end": offset + len(audio) / SR, "text": f"ช่วงที่ {len(calls)}", "words": [{"w": "ช่วง", "s": offset, "e": offset + 1, "p": 0.9}]}]  # fmt: skip

    monkeypatch.setattr(asr, "available", lambda: True)
    monkeypatch.setattr(asr, "transcribe_audio", transcribe_audio)
    return calls


def record(session, user, chunks, capture):
    for seq, data in enumerate(chunks, start=1):
        with capture(execute=True):
            services.add_chunk(session, user, 1, seq, data, 5)


@pytest.mark.django_db
def test_text_appears_while_recording_and_is_reused_after_closing(
    suspect, investigator, client_inv, browser_chunks, fake_model, django_capture_on_commit_callbacks, monkeypatch  # noqa: F811
):
    s = services.start(investigator, suspect, "ห้องสอบสวน 1", [])
    record(
        s,
        investigator,
        browser_chunks([(9, True), (1, False), (9, True), (1, False), (4, True)]),
        django_capture_on_commit_callbacks,
    )
    s.refresh_from_db()
    pauses = [(9.0, 10.0), (19.0, 20.0)]
    cuts = [round(a + n, 2) for a, n in fake_model]  # where each window ended while recording
    assert cuts and all(any(lo <= c <= hi for lo, hi in pauses) for c in cuts)  # never mid-sentence
    page = client_inv.get(reverse("interviews:live", args=[s.pk])).json()
    assert page["enabled"] and page["segments"][0]["text"] == "ช่วงที่ 1" and page["received"] > page["done"] > 0

    with django_capture_on_commit_callbacks(execute=True):  # the officer pressed "stop": the tail is done too
        assert client_inv.post(reverse("interviews:live", args=[s.pk])).json() == {"queued": True}
    s.refresh_from_db()
    windows = [(a, a + n) for a, n in fake_model]
    assert all(abs(b - c) < 0.05 for (_, b), (c, _) in zip(windows, windows[1:], strict=False))  # no gap, no overlap
    assert abs(s.live["takes"]["1"]["done"] - s.live["takes"]["1"]["received"]) < 0.05

    called = []
    monkeypatch.setattr(asr, "transcribe", lambda *a, **kw: called.append(kw) or [])
    with django_capture_on_commit_callbacks(execute=True):
        services.close_recording(s, investigator)
    s.refresh_from_db()
    assert called == []  # everything was transcribed while recording: nothing to redo
    assert [x.text for x in s.segments.order_by("start")] == [f"ช่วงที่ {i}" for i in range(1, len(fake_model) + 1)]
    assert "ถอดสดระหว่างบันทึกครบ" in next(st["note"] for st in s.progress if st["key"] == "asr")
    assert s.status == InterviewSession.Status.REVIEW and not LiveSegment.objects.filter(session=s).exists() and s.live == {}


@pytest.mark.django_db
def test_closing_before_live_caught_up_transcribes_only_the_rest(
    suspect, investigator, browser_chunks, fake_model, django_capture_on_commit_callbacks, monkeypatch  # noqa: F811
):
    s = services.start(investigator, suspect, "ห้องสอบสวน 1", [])
    record(
        s,
        investigator,
        browser_chunks([(9, True), (1, False), (9, True), (1, False), (4, True)]),
        django_capture_on_commit_callbacks,
    )
    s.refresh_from_db()
    covered = s.live["takes"]["1"]["done"]
    rest = []

    def transcribe(path, progress=None, start=0.0):
        rest.append(start)
        return [{"start": start, "end": start + 4, "text": "ส่วนท้าย", "words": []}]

    monkeypatch.setattr(asr, "transcribe", transcribe)
    with django_capture_on_commit_callbacks(execute=True):
        services.close_recording(s, investigator)
    s.refresh_from_db()
    assert rest == [pytest.approx(covered, abs=0.05)]
    assert [x.text for x in s.segments.order_by("start")][-1] == "ส่วนท้าย"
    assert "ถอดสดระหว่างบันทึก" in next(st["note"] for st in s.progress if st["key"] == "asr")


@pytest.mark.django_db
def test_one_live_pass_at_a_time_and_none_after_closing(suspect, investigator, fake_model):  # noqa: F811
    s = services.start(investigator, suspect, "ห้องสอบสวน 1", [])
    InterviewSession.objects.filter(pk=s.pk).update(live_lease=timezone.now() + live.LEASE)
    assert live.run(s.pk) == "busy"
    InterviewSession.objects.filter(pk=s.pk).update(live_lease=None, status="processing")
    assert live.run(s.pk) == "closed" and fake_model == []
    assert InterviewSession.objects.get(pk=s.pk).live_lease is None


@pytest.mark.django_db
def test_record_page_has_the_live_panel(suspect, investigator, client_inv):  # noqa: F811
    s = services.start(investigator, suspect, "ห้องสอบสวน 1", [])
    page = client_inv.get(reverse("interviews:record", args=[s.pk])).content.decode()
    assert "ข้อความถอดสด" in page and reverse("interviews:live", args=[s.pk]) in page and "ร่างโดย AI – รอตรวจ" in page
    assert client_inv.get(reverse("interviews:live", args=[s.pk])).json()["enabled"] is False  # fake ASR: off


@pytest.mark.skipif(not os.environ.get("CCID4_TEST_ASR_MODEL"), reason="set CCID4_TEST_ASR_MODEL to a CTranslate2 Whisper folder")
@pytest.mark.django_db
def test_live_with_the_real_speech_stack(
    suspect, investigator, browser_chunks, settings, django_capture_on_commit_callbacks  # noqa: F811
):
    settings.ASR_BACKEND, settings.ASR_DEVICE, settings.ASR_COMPUTE_TYPE = "whisper", "cpu", "int8"
    settings.ASR_MODEL = os.environ["CCID4_TEST_ASR_MODEL"]
    s = services.start(investigator, suspect, "ห้องสอบสวน 1", [])
    record(
        s,
        investigator,
        browser_chunks([(9, True), (1, False), (9, True), (1, False), (4, True)]),
        django_capture_on_commit_callbacks,
    )
    assert live.run(s.pk, flush=True) == "ok"
    s.refresh_from_db()
    take = s.live["takes"]["1"]
    assert not s.live.get("error") and take["done"] == pytest.approx(take["received"], abs=0.05) and take["done"] > 20
    with django_capture_on_commit_callbacks(execute=True):
        services.close_recording(s, investigator)
    s.refresh_from_db()
    assert s.status == InterviewSession.Status.REVIEW and not s.error
