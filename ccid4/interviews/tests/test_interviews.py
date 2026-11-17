"""Phase 8 (B2): acceptance checks from docs/SPEC_interview_recorder.md."""

import io
import json
import os
import shutil
import socket
import subprocess
import zipfile
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from docx import Document

from audit.models import AuditLog
from cases.tests.factories import CaseFactory, PartyFactory, PersonFactory
from interviews import qa, services
from interviews.documents import generate
from interviews.evaluation import cer
from interviews.models import InterviewSession, RecordingChunk, SegmentEdit

FIX = Path(__file__).resolve().parents[1] / "fixtures"
MULE = (FIX / "mule_account_suspect.json").read_text(encoding="utf-8")
NO_RIGHTS = (FIX / "mule_account_no_rights.json").read_text(encoding="utf-8")
HAS_FFMPEG = shutil.which("ffmpeg") is not None
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"


def tone(path, seconds, freq=440):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={seconds}",
            "-c:a",
            "libopus",
            str(path),
        ],
        check=True,
    )
    return Path(path).read_bytes()


@pytest.fixture
def suspect(investigator):
    case = CaseFactory(owner=investigator, offense="เปิดบัญชีให้ผู้อื่นใช้กระทำความผิด")
    return PartyFactory(
        case=case, role="suspect", person=PersonFactory(first_name="ทดสอบ", last_name="ระบบดี"), rights_informed_at=timezone.now()
    )


@pytest.fixture
def recorded(suspect, investigator, tmp_path, django_capture_on_commit_callbacks):
    """A session with a closed recording (two takes, as after a closed tab)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    s = services.start(investigator, suspect, "ห้องสอบสวน 1", [("lawyer", "ทนายสมมติ", ""), ("interpreter", "ล่ามสมมติ", "อังกฤษ")])
    services.add_chunk(s, investigator, 1, 1, tone(tmp_path / "a.webm", 4), 4)
    services.add_chunk(s, investigator, 2, 1, tone(tmp_path / "b.webm", 3, 660), 3)
    with django_capture_on_commit_callbacks(execute=True):
        services.close_recording(s, investigator)
    s.refresh_from_db()
    return s


def reviewed(s, user):
    for p in services.current_draft(s).pairs.all():
        services.update_pair(p, user, p.question, p.answer, True)


def doc_text(path):
    d = Document(path)
    body = "".join(t.text or "" for t in d.element.body.iter(W))
    sec = d.sections[0]
    return (
        body,
        "".join(p.text for p in sec.header.paragraphs),
        "".join(p.text for p in sec.footer.paragraphs),
        sec.different_first_page_header_footer,
    )


# ---------------------------------------------------------------- Q&A logic on the fixture (no audio models)
def segs_from(text, roles=None):
    roles = roles or {"S1": "investigator", "S2": "subject", "S3": "lawyer"}
    data = json.loads(text)["segments"]
    return [qa.Seg(i, d["start"], d["end"], roles[d["speaker"]], d["text"]) for i, d in enumerate(data, start=1)]


def test_official_first_person_without_paraphrasing():
    assert qa.to_official_answer("ใช่ครับ ผมเปิดบัญชีนี้") == "ข้าฯ ใช่ ข้าฯ เปิดบัญชีนี้"
    assert qa.to_official_answer("เอ่อ ดิฉันไม่ทราบค่ะ") == "ข้าฯ ไม่ทราบ"


def test_every_pair_has_its_source_and_figures_match_100_percent():
    segs = segs_from(MULE)
    by_id = {s.id: s for s in segs}
    pairs = qa.build_pairs(segs)
    assert len(pairs) >= 10
    assert all(p.segment_ids and p.end > p.start for p in pairs)
    text = " ".join(p.question + " " + p.answer for p in pairs)
    for figure in ("123-4-56789-0", "2,000", "150,000", "3 มีนาคม 2569", "0891234567"):
        assert figure in text
    assert all(qa.verify(p, by_id, all_segments=segs) == [] for p in pairs)
    assert qa.uncovered(segs, pairs) == []


def test_number_not_in_audio_is_flagged():
    segs = segs_from(MULE)
    by_id = {s.id: s for s in segs}
    pair = next(p for p in qa.build_pairs(segs) if "123-4-56789-0" in p.question)
    pair.answer = pair.answer.replace("3 มีนาคม 2569", "9 มีนาคม 2569")
    assert any("9" in f for f in qa.verify(pair, by_id, all_segments=segs))
    pair.question = pair.question.replace("123-4-56789-0", "123-4-99999-0")
    assert any("1234999990" in f for f in qa.verify(pair, by_id, all_segments=segs))


def test_invented_sentence_is_flagged():
    segs = segs_from(MULE)
    by_id = {s.id: s for s in segs}
    pair = qa.build_pairs(segs)[3]
    pair.answer += " ข้าฯ ได้ส่งมอบรหัสผ่านอินเทอร์เน็ตแบงก์กิ้งให้คนร้ายด้วย"
    assert any("หาต้นทาง" in f for f in qa.verify(pair, by_id, all_segments=segs))


def test_rights_found_or_missing():
    full = qa.check_rights(segs_from(MULE))
    assert all(r.ok for r in full) and full[0].key == "recording"
    missing = qa.check_rights(segs_from(NO_RIGHTS))
    assert not any(r.ok for r in missing)


def test_cer():
    assert cer("ข้าฯ เปิดบัญชี 123", "ข้าฯ เปิดบัญชี ๑๒๓") == 0
    assert 0 < cer("เปิดบัญชีธนาคาร", "เปิดบัญชีธนาคา") < 0.1


# ---------------------------------------------------------------- recording
@pytest.mark.django_db
def test_suspect_needs_rights_notice_before_recording(investigator):
    p = PartyFactory(case=CaseFactory(owner=investigator), role="suspect")
    with pytest.raises(ValidationError, match="แจ้งข้อกล่าวหาและสิทธิ"):
        services.start(investigator, p, "ห้อง", [])


@pytest.mark.django_db
def test_chunks_survive_a_closed_tab_and_resend_is_idempotent(suspect, investigator, client_inv):
    s = services.start(investigator, suspect, "ห้อง", [])
    url = reverse("interviews:chunk", args=[s.pk])
    blob = io.BytesIO(b"\x1aE\xdf\xa3fake-webm")
    blob.name = "c.webm"
    for _ in range(2):  # resent after a network hiccup
        blob.seek(0)
        r = client_inv.post(url, {"take": 1, "seq": 1, "seconds": "5", "blob": blob})
    assert r.json()["seconds"] == 5 and RecordingChunk.objects.filter(session=s).count() == 1
    status = client_inv.get(reverse("interviews:status", args=[s.pk])).json()  # page reopened later
    assert status == {"seconds": 5.0, "take": 1, "last_seq": 1, "bytes": status["bytes"], "closed": False, "state": "recording"}
    assert "บันทึกต่อ" in client_inv.get(reverse("interviews:record", args=[s.pk])).content.decode()


@pytest.mark.django_db
def test_close_locks_audio_with_sha256(recorded, investigator):
    rec = recorded.recording
    assert 6 < rec.duration < 8 and len(rec.sha256) == 64 and services.integrity_ok(rec)
    assert os.stat(rec.original.path).st_mode & 0o222 == 0  # read-only
    assert not recorded.chunks.exists()
    with pytest.raises(ValidationError):
        services.add_chunk(recorded, investigator, 3, 1, b"x", 1)
    with pytest.raises(ValidationError):
        services.close_recording(recorded, investigator)
    assert recorded.status == "transcript"  # no speech model here: waits for a transcript


@pytest.mark.django_db
def test_audio_is_served_with_ranges_and_every_listen_is_logged(recorded, client_inv):
    url = reverse("interviews:audio", args=[recorded.pk])
    full = client_inv.get(url)
    assert full.status_code == 200 and b"".join(full.streaming_content)[:4] == b"\x1aE\xdf\xa3"
    part = client_inv.get(url, HTTP_RANGE="bytes=10-19")
    assert part.status_code == 206 and len(b"".join(part.streaming_content)) == 10
    assert AuditLog.objects.filter(action="listen", object_type="interview").count() == 1  # the range request is the same listen


# ---------------------------------------------------------------- review and confirm
@pytest.mark.django_db
def test_import_review_confirm_and_documents(recorded, investigator, django_capture_on_commit_callbacks):
    services.import_transcript(recorded, investigator, MULE)
    recorded.refresh_from_db()
    assert recorded.status == "review" and recorded.rights.filter(found_at__isnull=True).count() == 0
    seg = recorded.segments.first()
    services.edit_segment(seg, investigator, seg.text + " (แก้)")
    seg.refresh_from_db()
    assert seg.text_ai != seg.text and SegmentEdit.objects.filter(segment=seg).exists()
    with pytest.raises(ValidationError, match="ยังไม่ได้ตรวจ"):
        services.confirm(recorded, investigator)
    reviewed(recorded, investigator)
    with django_capture_on_commit_callbacks(execute=True):
        st = services.confirm(recorded, investigator)
    cp = recorded.party
    assert cp.current_statement == st and st.source == "voice" and st.confirmed_by == investigator and not st.needs_review
    assert "ถาม" in st.text and "ข้าฯ" in st.text and "123-4-56789-0" in st.text
    doc = recorded.documents.get()
    body, header, footer, diff_first = doc_text(doc.docx.path)
    assert "{" not in body + header + footer
    assert header == f"ใบต่อคำให้การ ของ {cp.person.full_name}" and diff_first  # page 2+ only
    assert recorded.recording.sha256 in footer and doc.code in footer
    assert "ล่ามสมมติ ล่ามภาษาอังกฤษ" in body and "150,000" in body
    with zipfile.ZipFile(doc.evidence_zip.path) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["audio_sha256_at_close"] == recorded.recording.sha256
        audio_name = next(n for n in z.namelist() if n.startswith("audio/"))
        import hashlib

        assert hashlib.sha256(z.read(audio_name)).hexdigest() == recorded.recording.sha256 == manifest["files"][audio_name]
        assert {"transcript.json", "transcript.docx"} <= set(z.namelist())
    assert recorded.case.tasks.filter(source="auto", title__contains="ด้วยเสียง").exists()


@pytest.mark.django_db
def test_missing_rights_need_a_reason(recorded, investigator, django_capture_on_commit_callbacks):
    services.import_transcript(recorded, investigator, NO_RIGHTS)
    reviewed(recorded, investigator)
    with pytest.raises(ValidationError, match="แจ้งสิทธิ"):
        services.confirm(recorded, investigator)
    with django_capture_on_commit_callbacks(execute=True):
        services.confirm(recorded, investigator, "ฟังแล้ว แจ้งสิทธิก่อนเริ่มบันทึกตามบันทึกแจ้งสิทธิแผ่นที่ 2")
    recorded.refresh_from_db()
    assert recorded.status == "confirmed" and "แผ่นที่ 2" in recorded.rights_override


@pytest.mark.django_db
def test_screens(recorded, investigator, client_inv):
    services.import_transcript(recorded, investigator, MULE)
    html = client_inv.get(reverse("interviews:review", args=[recorded.pk])).content.decode()
    assert "การแจ้งสิทธิที่ตรวจพบในเสียง" in html and "ร่างโดย AI – รอตรวจ" in html and "data-waveform" in html
    pair = services.current_draft(recorded).pairs.get(order=3)
    r = client_inv.post(reverse("interviews:save_pair", args=[recorded.pk, pair.pk]),
                        {"question": pair.question, "answer": pair.answer + " เมื่อวันที่ 99 มีนาคม", "reviewed": "on"})  # fmt: skip
    assert "ไม่พบในเสียง" in r.content.decode()
    r = client_inv.post(reverse("interviews:redraft", args=[recorded.pk]), {"mode": "written"})
    assert services.current_draft(recorded).version == 2 and services.current_draft(recorded).mode == "written"


@pytest.mark.django_db
def test_demo_transcript_and_permissions(recorded, client, client_inv, make_user, settings):
    settings.DEMO_MODE = True
    r = client_inv.post(reverse("interviews:import_transcript", args=[recorded.pk]), {"demo": "1"})
    assert r["Location"] == reverse("interviews:review", args=[recorded.pk])
    other = make_user(role="investigator")
    client.force_login(other)
    assert client.get(reverse("interviews:review", args=[recorded.pk])).status_code == 403
    commander = make_user(role="commander")
    client.force_login(commander)
    assert client.get(reverse("interviews:review", args=[recorded.pk])).status_code == 200
    assert client.post(reverse("interviews:redraft", args=[recorded.pk])).status_code == 403


@pytest.mark.django_db
def test_whole_pipeline_runs_without_network(recorded, investigator, monkeypatch, django_capture_on_commit_callbacks):
    """SPEC: ไม่มี request ออกนอกเครื่อง — block every non-local connection and run import → draft → confirm → documents."""
    real_connect = socket.socket.connect

    def guarded(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and not (host.startswith(("127.", "::1", "localhost", "/")) or host == ""):
            raise AssertionError(f"network call to {host}")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    services.import_transcript(recorded, investigator, MULE)
    reviewed(recorded, investigator)
    with django_capture_on_commit_callbacks(execute=True):
        services.confirm(recorded, investigator)
    assert recorded.documents.exists()


@pytest.mark.django_db
def test_regenerating_documents_keeps_the_same_audio_hash(recorded, investigator, django_capture_on_commit_callbacks):
    services.import_transcript(recorded, investigator, MULE)
    reviewed(recorded, investigator)
    with django_capture_on_commit_callbacks(execute=True):
        services.confirm(recorded, investigator)
    again = generate(recorded.pk, investigator.pk)
    assert again.hashes[next(k for k in again.hashes if k.startswith("audio/"))] == recorded.recording.sha256
    assert InterviewSession.objects.get(pk=recorded.pk).locked
