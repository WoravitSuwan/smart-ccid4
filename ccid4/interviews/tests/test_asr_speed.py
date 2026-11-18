"""ถอดเสียงบน CPU ให้เร็วขึ้น และงานที่ค้าง (เครื่องถอดเสียงหยุด) เริ่มใหม่ได้จากหน้า "ประมวลผล"."""

import os
import shutil
import subprocess
import sys
import types
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone

from ai import asr
from audit.models import AuditLog
from interviews import services
from interviews.models import InterviewSession

from .test_interviews import recorded, suspect  # noqa: F401 - fixtures

HYPER_THREADED = "".join(
    f"processor\t: {i}\nphysical id\t: 0\ncore id\t\t: {i // 2}\nsiblings\t: 8\ncpu cores\t: 4\n\n" for i in range(8)
)


def test_physical_cores_ignores_hyper_threads(tmp_path, monkeypatch):
    info = tmp_path / "cpuinfo"
    info.write_text(HYPER_THREADED)
    monkeypatch.setattr(asr.os, "sched_getaffinity", lambda pid: set(range(8)), raising=False)
    assert asr.physical_cores(str(info)) == 4
    monkeypatch.setattr(asr.os, "sched_getaffinity", lambda pid: {0, 1}, raising=False)  # container limited to 2 CPUs
    assert asr.physical_cores(str(info)) == 2
    assert asr.physical_cores(str(tmp_path / "missing")) == 2


def test_cpu_defaults_are_fast_and_explicit_settings_win(settings, monkeypatch):
    monkeypatch.setattr(asr, "physical_cores", lambda: 6)
    settings.ASR_DEVICE, settings.ASR_BEAM_SIZE, settings.ASR_CPU_THREADS = "cpu", 0, 0
    assert (asr.cpu_threads(), asr.beam_size(), asr.profile()) == (6, 1, "CPU 6 แกน · beam 1")
    settings.ASR_DEVICE = "cuda"
    assert asr.beam_size() == 5
    settings.ASR_DEVICE, settings.ASR_BEAM_SIZE, settings.ASR_CPU_THREADS = "cpu", 5, 3
    assert (asr.cpu_threads(), asr.beam_size()) == (3, 5)


def test_transcribe_passes_threads_and_beam_and_reports_start(settings, monkeypatch, tmp_path):
    seen = {}

    class Model:
        def __init__(self, path, **kw):
            seen["init"] = kw

        def transcribe(self, path, **kw):
            seen["run"] = kw
            seg = types.SimpleNamespace(start=0.0, end=4.0, text=" สวัสดีครับ ", words=[])
            return iter([seg]), types.SimpleNamespace(duration=8.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(asr, "physical_cores", lambda: 8)
    monkeypatch.setattr(asr, "load_audio", lambda path: [0.0] * 16000)
    settings.ASR_BACKEND, settings.ASR_MODEL, settings.ASR_DEVICE = "whisper", str(tmp_path), "cpu"
    settings.ASR_BEAM_SIZE = settings.ASR_CPU_THREADS = 0
    calls = []
    out = asr.transcribe(tmp_path / "a.flac", progress=lambda done, total: calls.append((done, total)))
    assert seen["init"]["cpu_threads"] == 8 and seen["run"]["beam_size"] == 1
    assert calls == [(0.0, 8.0), (4.0, 8.0)] and out[0]["text"] == "สวัสดีครับ"


@pytest.mark.django_db
def test_progress_shows_time_left(recorded, monkeypatch):  # noqa: F811
    recorded.progress = [{"key": "asr", "label": "ถอดเสียง", "state": "running", "note": ""}]
    clock = iter([100.0, 130.0, 160.0])
    monkeypatch.setattr(services.time, "monotonic", lambda: next(clock))
    report = services.asr_progress(recorded, 600)
    report(0, 600)  # model ready
    assert "เริ่มถอดเสียง" in recorded.progress[0]["note"]
    report(60, 600)  # 60 s of audio in 30 s of work: 540 s left at that pace → about 4.5 minutes
    assert "10%" in recorded.progress[0]["note"] and "เหลือประมาณ 4 นาที" in recorded.progress[0]["note"]
    report(120, 600)
    assert "20%" in recorded.progress[0]["note"] and "เหลือประมาณ 4 นาที" in recorded.progress[0]["note"]


@pytest.mark.django_db
def test_stalled_job_can_be_restarted(recorded, client_inv, investigator, django_capture_on_commit_callbacks):  # noqa: F811
    old = (timezone.now() - timedelta(minutes=12)).isoformat(timespec="seconds")
    recorded.status, recorded.job = InterviewSession.Status.PROCESSING, "deadjob"
    recorded.progress = [{"key": k, "label": label, "state": "waiting", "note": "", "at": old} for k, label in services.STEPS]
    recorded.save()
    assert services.minutes_without_progress(recorded) >= 12
    page = client_inv.get(reverse("interviews:process", args=[recorded.pk])).content.decode()
    assert "ไม่มีความคืบหน้ามา 12 นาที" in page and "เริ่มถอดเสียงใหม่" in page
    with django_capture_on_commit_callbacks(execute=True):
        r = client_inv.post(reverse("interviews:reprocess", args=[recorded.pk]))
    assert r.status_code == 302
    recorded.refresh_from_db()
    assert recorded.job not in ("", "deadjob") and recorded.status == InterviewSession.Status.TRANSCRIPT  # fake ASR ran
    assert AuditLog.objects.filter(user=investigator, detail__contains="เริ่มประมวลผลเสียงใหม่").exists()
    assert client_inv.post(reverse("interviews:reprocess", args=[recorded.pk])).status_code == 302  # not processing: refused


@pytest.mark.django_db
def test_old_run_stops_when_a_new_one_starts(recorded):  # noqa: F811
    InterviewSession.objects.filter(pk=recorded.pk).update(status="processing", job="newer")
    assert services.process(recorded.pk, "older").job == "newer"  # queued before the restart: does nothing
    recorded.job = "older"  # an old run still working when the officer restarted
    with pytest.raises(services.Superseded):
        services.set_step(recorded, "asr", "running", "ถอดเสียงแล้ว 01:00")
    legacy = InterviewSession.objects.get(pk=recorded.pk)
    legacy.progress = [{"key": "asr", "label": "ถอดเสียง", "state": "running", "note": ""}]  # older version: no time
    assert services.minutes_without_progress(legacy) is not None


# ---------------------------------------------------------------- the speech model gets decoded audio, never a file
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_load_audio_decodes_with_ffmpeg(tmp_path):
    clip = tmp_path / "a.webm"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:a", "libopus", str(clip)], check=True)  # fmt: skip
    audio = asr.load_audio(clip)
    assert audio.dtype.name == "float32" and abs(len(audio) - 32000) < 1600 and 0.05 < abs(audio).max() <= 1.0


@pytest.mark.django_db
def test_unexpected_error_is_shown_and_can_be_retried(recorded, client_inv, monkeypatch, settings):  # noqa: F811
    settings.CELERY_TASK_ALWAYS_EAGER = True
    monkeypatch.setattr(asr, "available", lambda: True)

    def broken(path, progress=None):
        raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")

    monkeypatch.setattr(asr, "transcribe", broken)
    InterviewSession.objects.filter(pk=recorded.pk).update(status="processing", job="", error="")
    s = services.process(recorded.pk)
    asr_step = next(st for st in s.progress if st["key"] == "asr")
    assert s.status == InterviewSession.Status.TRANSCRIPT and "ไฟล์เสียงต้นฉบับไม่หาย" in s.error
    assert asr_step["state"] == "error" and "metadata_errors" in asr_step["note"]
    page = client_inv.get(reverse("interviews:process", args=[s.pk])).content.decode()
    assert "step-error" in page and "เริ่มถอดเสียงใหม่" in page and services.can_restart(s)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_setup_asr_runs_a_real_transcription_check(monkeypatch, tmp_path):
    runs = []

    class Model:
        def __init__(self, path, **kw):
            pass

        def transcribe(self, audio, **kw):
            runs.append((len(audio), kw["vad_filter"]))
            return iter([]), types.SimpleNamespace(duration=2.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    (tmp_path / "asr" / "small").mkdir(parents=True)
    (tmp_path / "asr" / "small" / "model.bin").write_bytes(b"x")
    call_command("setup_asr", model="small", out=str(tmp_path))
    assert [vad for _, vad in runs] == [True, False] and runs[0][0] > 30000  # 2 s decoded at 16 kHz

    class Broken(Model):
        def transcribe(self, audio, **kw):
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Broken))
    with pytest.raises(CommandError, match="ทดสอบถอดเสียงไม่สำเร็จ"):
        call_command("setup_asr", model="small", out=str(tmp_path))


# ---------------------------------------------------------------- opt-in: the real speech stack (faster-whisper + model)
REAL_MODEL = os.environ.get("CCID4_TEST_ASR_MODEL", "")


@pytest.mark.skipif(not REAL_MODEL, reason="set CCID4_TEST_ASR_MODEL to a CTranslate2 Whisper folder to run")
@pytest.mark.django_db
def test_real_model_runs_the_whole_pipeline(recorded, settings):  # noqa: F811
    """The real faster-whisper path on the recorded session (a tone: no speech, so the draft is empty but nothing fails)."""
    settings.ASR_BACKEND, settings.ASR_MODEL, settings.ASR_DEVICE, settings.ASR_COMPUTE_TYPE = (
        "whisper",
        REAL_MODEL,
        "cpu",
        "int8",
    )
    InterviewSession.objects.filter(pk=recorded.pk).update(status="processing", job="", error="")
    s = services.process(recorded.pk)
    steps = {st["key"]: st["state"] for st in s.progress}
    assert s.error == "" and steps["asr"] == "done" and s.status == InterviewSession.Status.REVIEW, (s.error, s.progress)
