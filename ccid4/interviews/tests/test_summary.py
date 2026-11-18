"""สรุปใจความสำคัญ · คำให้การแบบเล่าเรื่อง · คำที่พบบ่อย · ผู้แจ้งความรายใหม่ (ข้อมูลจำลองทั้งหมด)."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from docx import Document

from audit.models import AuditLog
from cases.models import CaseStatus
from documents import engine
from interviews import documents, qa, services, summary
from interviews.models import InterviewSession, Segment

from .test_interviews import HAS_FFMPEG, W, tone

FIX = Path(__file__).resolve().parents[1] / "fixtures"
ACCUSER = (FIX / "online_fraud_accuser.json").read_text(encoding="utf-8")


def segs_of(text):
    roles = {"S1": "investigator", "S2": "subject"}
    return [
        qa.Seg(i, d["start"], d["end"], roles[d["speaker"]], d["text"]) for i, d in enumerate(json.loads(text)["segments"], 1)
    ]


def body_text(data: bytes) -> str:
    return "".join(t.text or "" for t in Document(io.BytesIO(data)).element.body.iter(W))


# ---------------------------------------------------------------- pure functions
def test_keywords_skip_particles_pronouns_and_numbers():
    words = dict(summary.keywords(["ผมโอนเงินให้ผู้ขายครับ ผมโอนเงินไป 2 ครั้งครับ ผู้ขายไม่ส่งของ ผู้ขายบล็อกไลน์ค่ะ"]))
    assert words["ผู้ขาย"] == 3 and words["โอน"] == 2
    assert not {"ครับ", "ค่ะ", "ผม", "2"} & set(words)


def test_speaker_is_guessed_without_a_diarization_model():
    assert summary.guess_speaker("โอนเงินไปเมื่อไหร่ เท่าไหร่ครับ") == "S1"
    assert summary.guess_speaker("พนักงานสอบสวนขอแจ้งว่าการสอบปากคำนี้มีการบันทึกเสียง") == "S1"
    assert summary.guess_speaker("ดิฉันโอนเงินไป 8,500 บาทค่ะ") == "S2"


def test_points_and_story_come_from_the_recording_only():
    segs = segs_of(ACCUSER)
    pairs = qa.build_pairs(segs)
    points = summary.code_points(pairs, segs)
    joined = "\n".join(points)
    for fact in ("8,500 บาท", "123-4-56789-0", "กสิกรไทย", "0812345678", "12 ตุลาคม 2569", "เฟซบุ๊ก", "ไลน์", "ความประสงค์"):
        assert fact in joined
    assert "เลขบัญชี: 123-4-56789-0\n" in joined + "\n"  # a phone number is not listed as an account
    story = summary.code_narrative(pairs, segs)
    assert story.startswith("ข้าฯ ขอให้การว่า ") and "ดิฉัน" not in story and "ค่ะ" not in story
    assert "8,500 บาท" in story and len(story.split("\n")) >= 2
    assert summary.verify_text(story, segs) == []


def test_story_with_a_changed_figure_or_an_invented_sentence_is_flagged():
    segs = segs_of(ACCUSER)
    story = summary.code_narrative(qa.build_pairs(segs), segs)
    assert any("9,500" in f for f in summary.verify_text(story.replace("8,500", "9,500"), segs))
    assert any("หาต้นทาง" in f for f in summary.verify_text(story + " ข้าฯ ได้มอบรหัสผ่านธนาคารให้ผู้ขายทางโทรศัพท์ด้วย", segs))


def test_pronoun_after_a_word_becomes_official_first_person():
    assert qa.to_official_answer("ผู้ขายบล็อกไลน์ดิฉันค่ะ") == "ข้าฯ ผู้ขายบล็อกไลน์ ข้าฯ"
    assert "ข้าฯ ก็ไม่ได้ใช้" in qa.to_official_answer("หลังจากส่งไปผมก็ไม่ได้ใช้บัญชีเลยครับ")


def test_engine_drops_an_empty_repeat_table_and_splits_story_paragraphs():
    from docx import Document as New

    d = New()
    d.add_paragraph("{เรื่องเล่า}")
    t = d.add_table(rows=1, cols=1)
    t.rows[0].cells[0].paragraphs[0].add_run("ถาม {qa.ถาม}")
    buf = io.BytesIO()
    d.save(buf)
    out, missing = engine.fill(buf.getvalue(), {"เรื่องเล่า": "ย่อหน้าหนึ่ง\nย่อหน้าสอง", "qa": []}, drop_empty_rows={"qa"})
    doc = Document(io.BytesIO(out))
    assert not missing and not doc.tables
    assert [p.text for p in doc.paragraphs if p.text] == ["ย่อหน้าหนึ่ง", "ย่อหน้าสอง"]


# ---------------------------------------------------------------- whole flow: ผู้แจ้งความรายใหม่ → เสียง → เล่าเรื่อง → Word
@pytest.fixture
def complainant(investigator, tmp_path, django_capture_on_commit_callbacks):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    s = services.quick_start(
        investigator, "นางสาวทดลอง ใจดี", "0800000000", "online", "ถูกหลอกซื้อโทรศัพท์ออนไลน์", "ห้องสอบสวน 2"
    )
    services.add_chunk(s, investigator, 1, 1, tone(tmp_path / "a.webm", 5), 5)
    with django_capture_on_commit_callbacks(execute=True):
        services.close_recording(s, investigator)
    s.refresh_from_db()
    services.import_transcript(s, investigator, ACCUSER)
    s.refresh_from_db()
    return s


@pytest.mark.django_db
def test_quick_start_creates_a_received_case_with_the_complainant(complainant):
    case = complainant.case
    assert case.status == CaseStatus.RECEIVED and case.title == "ถูกหลอกซื้อโทรศัพท์ออนไลน์"
    assert complainant.party.role == "accuser" and complainant.party.person.phone == "0800000000"
    assert complainant.party.person.full_name.endswith("ทดลอง ใจดี")
    assert complainant.statement_format == "narrative" and complainant.status == "review"


@pytest.mark.django_db
def test_narrative_statement_needs_review_then_goes_into_word(complainant, investigator, django_capture_on_commit_callbacks):
    draft = services.current_draft(complainant)
    assert draft.points and draft.narrative.startswith("ข้าฯ ขอให้การว่า") and draft.keywords and draft.narrative_flags == []
    with pytest.raises(ValidationError, match="ฟังแล้ว ตรงกับเสียง"):
        services.confirm(complainant, investigator)
    services.update_narrative(draft, investigator, draft.narrative, True)
    with django_capture_on_commit_callbacks(execute=True):
        st = services.confirm(complainant, investigator)
    assert st.text == draft.narrative and not st.text.startswith("ถาม") and "\nตอบ" not in st.text
    doc = complainant.documents.get()
    with open(doc.docx.path, "rb") as f:
        data = f.read()
    text = body_text(data)
    assert "{" not in text and "ข้าฯ ขอให้การว่า" in text and "8,500 บาท" in text and "ถามประสงค์" not in text
    assert not Document(io.BytesIO(data)).tables  # no Q&A table in a narrative statement
    with zipfile.ZipFile(doc.evidence_zip.path) as z:
        name = next(n for n in z.namelist() if n.startswith("สรุปการสอบปากคำ-"))
        summary_text = body_text(z.read(name))
    assert "สรุปใจความสำคัญ" in summary_text and "คำที่พบบ่อย" in summary_text and "ผู้ขาย" in summary_text
    assert "ตรวจแล้วโดย" in summary_text and "ร่างโดย AI" not in summary_text


@pytest.mark.django_db
def test_summary_word_is_labelled_draft_until_confirmed(complainant):
    text = body_text(documents.summary_docx(complainant, services.current_draft(complainant)))
    assert "ร่างโดย AI – รอตรวจ" in text and "เอกสารประกอบการทำสำนวน ไม่ใช่บันทึกคำให้การ" in text
    assert "8,500 บาท" in text and "123-4-56789-0" in text


@pytest.mark.django_db
def test_transcript_without_speaker_labels_is_split_by_guess(complainant, investigator):
    lines = "\n".join(f"[00:{i * 5:02d}] {d['text']}" for i, d in enumerate(json.loads(ACCUSER)["segments"]))
    services.import_transcript(complainant, investigator, lines)
    speakers = list(complainant.segments.values_list("speaker", flat=True))
    assert speakers[:4] == ["S1", "S2", "S1", "S2"]


# ---------------------------------------------------------------- screens
@pytest.mark.django_db
def test_quick_start_screen(client_inv):
    html = client_inv.get(reverse("interviews:index")).content.decode()
    assert "อัดเสียงผู้แจ้งความรายใหม่" in html
    r = client_inv.post(
        reverse("interviews:quick_start"), {"name": "นายสมมติ ทดสอบ", "title": "ถูกหลอกลงทุนออนไลน์", "kind": "online"}
    )
    s = InterviewSession.objects.latest("pk")
    assert r["Location"] == reverse("interviews:record", args=[s.pk])
    assert client_inv.post(reverse("interviews:quick_start"), {"name": "", "title": ""})["Location"] == reverse(
        "interviews:index"
    )


@pytest.mark.django_db
def test_review_screen_summary_story_and_words(complainant, client_inv, investigator):
    html = client_inv.get(reverse("interviews:review", args=[complainant.pk])).content.decode()
    for text in (
        "สรุปใจความสำคัญ",
        "เล่าเรื่อง",
        "คำที่พบบ่อย",
        'data-kw="ผู้ขาย"',
        "บรรยาย (เล่าเรื่อง)",
        "ร่างโดย AI – รอตรวจ",
    ):
        assert text in html
    draft = services.current_draft(complainant)
    r = client_inv.post(
        reverse("interviews:narrative", args=[complainant.pk]),
        {"narrative": draft.narrative + " โอนอีก 99,999 บาท", "reviewed": "on"},
    )
    html = r.content.decode()
    assert "บันทึกแล้ว" in html and "99,999" in html and "ไม่พบในเสียง" in html and 'hx-swap-oob="true"' in html
    seg = complainant.segments.get(idx=2)
    r = client_inv.post(reverse("interviews:segment_speaker", args=[complainant.pk, seg.pk]), {"speaker": "S1"})
    assert Segment.objects.get(pk=seg.pk).speaker == "S1" and "ร่างใหม่" in r.content.decode()
    client_inv.post(reverse("interviews:statement_format", args=[complainant.pk]), {"format": "qa"})
    assert InterviewSession.objects.get(pk=complainant.pk).statement_format == "qa"
    r = client_inv.get(reverse("interviews:summary", args=[complainant.pk]))
    assert r.status_code == 200 and r["Content-Type"].startswith("application/vnd.openxmlformats")
    assert AuditLog.objects.filter(action="export", detail__contains="สรุปการสอบปากคำ").exists()


@pytest.mark.django_db
def test_demo_warning_on_the_recording_page(client_inv, investigator, settings):
    s = services.quick_start(investigator, "นายสมมติ ทดสอบ", "", "online", "ทดสอบระบบสาธิต", "")
    settings.DEMO_MODE = True
    assert "ห้ามบันทึกเสียงหรือข้อมูลของผู้แจ้งจริง" in client_inv.get(reverse("interviews:record", args=[s.pk])).content.decode()
    settings.DEMO_MODE = False
    assert (
        "ห้ามบันทึกเสียงหรือข้อมูลของผู้แจ้งจริง"
        not in client_inv.get(reverse("interviews:record", args=[s.pk])).content.decode()
    )


# ---------------------------------------------------------------- long recordings: progress while the model works
def test_transcribe_reports_progress_and_uses_the_cpu_settings(monkeypatch, settings, tmp_path):
    import sys
    import types

    from ai import asr

    seen = {}

    class Word:
        def __init__(self, w, s, e):
            self.word, self.start, self.end, self.probability = w, s, e, 0.9

    class Seg:
        def __init__(self, s, e, text):
            self.start, self.end, self.text, self.words = s, e, text, [Word(text, s, e)]

    class Model:
        def __init__(self, path, **kw):
            seen["init"] = (path, kw)

        def transcribe(self, path, **kw):
            seen["kw"] = kw
            return iter([Seg(0, 30, "สวัสดีครับ"), Seg(1800, 1830, "ตอนท้าย")]), types.SimpleNamespace(duration=3600.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(asr, "load_audio", lambda path: [0.0] * 16000)
    settings.ASR_BACKEND, settings.ASR_MODEL, settings.ASR_DEVICE = "whisper", "/models/asr/large-v3-turbo", "cpu"
    settings.ASR_COMPUTE_TYPE, settings.ASR_BEAM_SIZE, settings.ASR_CPU_THREADS = "int8", 1, 4
    ticks = []
    out = asr.transcribe(tmp_path / "a.flac", progress=lambda done, total: ticks.append((done, total)))
    assert [s["text"] for s in out] == ["สวัสดีครับ", "ตอนท้าย"] and out[1]["words"][0]["s"] == 1800
    assert ticks == [(0.0, 3600.0), (30.0, 3600.0), (1830.0, 3600.0)]  # 0 = model ready, decoding starts
    assert seen["init"] == ("/models/asr/large-v3-turbo", {"device": "cpu", "compute_type": "int8", "cpu_threads": 4})
    assert seen["kw"]["beam_size"] == 1 and seen["kw"]["language"] == "th" and seen["kw"]["vad_filter"]
