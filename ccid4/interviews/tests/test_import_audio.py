"""นำเข้าไฟล์เสียงที่อัดไว้แล้ว (มือถือ เครื่องอัดเสียง): ต้นฉบับตามที่ได้รับทุกไบต์ → SHA-256 → ล็อก → ถอดเสียง."""

import hashlib
import os
import shutil
import stat
import subprocess

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from ai import asr
from audit.models import AuditLog
from evidence.models import EvidenceFile
from interviews import services
from interviews.models import InterviewSession, Recording

from .test_interviews import suspect  # noqa: F401 - fixture

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def clip(tmp_path, name, *codec):
    path = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=3", *codec, str(path)], check=True
    )
    return path.read_bytes()


@pytest.fixture
def room(suspect, investigator):  # noqa: F811
    return services.start(investigator, suspect, "ห้องสอบสวน 1", [])


def post(client, room, name, data, capture):
    with capture(execute=True):
        return client.post(reverse("interviews:import_audio", args=[room.pk]), {"audio": SimpleUploadedFile(name, data)})


@pytest.mark.django_db
@pytest.mark.parametrize("name,codec", [("บันทึกเสียง 001.m4a", ("-c:a", "aac")), ("voice.wav", ())])
def test_phone_recording_is_locked_as_evidence_and_transcribed(
    room, investigator, client_inv, tmp_path, django_capture_on_commit_callbacks, name, codec
):
    data = clip(tmp_path, "x" + os.path.splitext(name)[1], *codec)
    r = post(client_inv, room, name, data, django_capture_on_commit_callbacks)
    assert r.status_code == 302 and r.url == reverse("interviews:process", args=[room.pk])
    rec = Recording.objects.get(session=room)
    assert rec.source == "import" and rec.source_name == name and rec.size == len(data)
    assert rec.sha256 == hashlib.sha256(data).hexdigest()
    assert open(rec.original.path, "rb").read() == data  # the original exactly as received
    assert not os.stat(rec.original.path).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)  # read-only
    assert abs(rec.duration - 3.0) < 0.3
    assert EvidenceFile.objects.filter(case=room.case, sha256=rec.sha256, origin="interview").exists()
    assert AuditLog.objects.filter(user=investigator, action="import", detail__contains=rec.sha256).exists()
    room.refresh_from_db()
    assert (
        room.status == InterviewSession.Status.TRANSCRIPT and rec.__class__.objects.get(pk=rec.pk).for_asr
    )  # fake ASR: ready for a transcript


@pytest.mark.django_db
def test_imported_file_goes_through_the_same_transcription(
    room, client_inv, tmp_path, django_capture_on_commit_callbacks, monkeypatch
):
    monkeypatch.setattr(asr, "available", lambda: True)
    monkeypatch.setattr(
        asr,
        "transcribe",
        lambda path, progress=None, start=0.0: [{"start": 0.0, "end": 3.0, "text": "ข้าฯ ขอให้การว่า", "words": []}],
    )
    post(client_inv, room, "memo.m4a", clip(tmp_path, "a.m4a", "-c:a", "aac"), django_capture_on_commit_callbacks)
    room.refresh_from_db()
    assert room.status == InterviewSession.Status.REVIEW and room.segments.get().text == "ข้าฯ ขอให้การว่า"
    assert client_inv.get(reverse("interviews:transcript_docx", args=[room.pk])).status_code == 200


@pytest.mark.django_db
def test_files_without_sound_or_of_other_kinds_are_refused(room, client_inv, tmp_path, django_capture_on_commit_callbacks):
    r = post(client_inv, room, "ไม่ใช่เสียง.mp3", b"this is not audio", django_capture_on_commit_callbacks)
    assert r.url == reverse("interviews:record", args=[room.pk]) and not Recording.objects.filter(session=room).exists()
    page = client_inv.get(r.url).content.decode()
    assert "ไม่มีเสียงอยู่ในไฟล์" in page
    post(client_inv, room, "notes.txt", b"hello", django_capture_on_commit_callbacks)
    assert "รองรับไฟล์" in client_inv.get(r.url).content.decode() and not Recording.objects.filter(session=room).exists()


@pytest.mark.django_db
def test_one_room_holds_either_a_recording_made_here_or_an_imported_file(
    room, investigator, client_inv, tmp_path, django_capture_on_commit_callbacks
):
    assert "นำเข้าไฟล์เสียงที่อัดไว้แล้ว" in client_inv.get(reverse("interviews:record", args=[room.pk])).content.decode()
    services.add_chunk(room, investigator, 1, 1, clip(tmp_path, "c.webm", "-c:a", "libopus"), 5)
    assert "นำเข้าไฟล์เสียงที่อัดไว้แล้ว" not in client_inv.get(reverse("interviews:record", args=[room.pk])).content.decode()
    post(client_inv, room, "memo.wav", clip(tmp_path, "m.wav"), django_capture_on_commit_callbacks)
    assert not Recording.objects.filter(session=room).exists()


@pytest.mark.django_db
def test_formats_browsers_cannot_play_are_heard_through_the_flac_copy(
    room, client_inv, tmp_path, django_capture_on_commit_callbacks
):
    try:
        data = clip(tmp_path, "a.3gp", "-c:a", "aac", "-f", "3gp")
    except subprocess.CalledProcessError:
        pytest.skip("ffmpeg without a 3gp muxer")
    post(client_inv, room, "android.3gp", data, django_capture_on_commit_callbacks)
    r = client_inv.get(reverse("interviews:audio", args=[room.pk]))
    assert r.status_code == 200 and r["Content-Type"] == "audio/flac"
