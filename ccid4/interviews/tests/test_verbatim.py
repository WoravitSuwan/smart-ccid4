"""คำพูดคำต่อคำ: ผู้แจ้งเล่า → ได้ข้อความตามที่พูดก่อน แล้วพนักงานสอบสวนค่อยสรุปเป็นสำนวน."""

import io
import re

import pytest
from django.urls import reverse
from docx import Document

from audit.models import AuditLog
from interviews import services

from .test_interviews import MULE, recorded, suspect  # noqa: F401 - fixtures

TEXT = "ข้าฯ ก็เลยโอนเงินไป 3,000 บาท"
# what faster-whisper gives for Thai: token pieces, often parts of a word
PIECES = ["ข้", "าฯ", "ก็", "เลย", "โอ", "น", "เงิน", "ไป", "3", ",000", "บาท"]


def shown(words):
    return "".join((" " if w["sp"] else "") + w["w"] for w in words)


def test_thai_pieces_read_as_the_sentence_not_split_words():
    words = [{"w": w, "s": i, "e": i + 0.5, "p": 0.9} for i, w in enumerate(PIECES)]
    out = services.display_words(TEXT, words)
    assert shown(out) == TEXT  # not "ข้ าฯ ก็ เลย โอ น เงิน …"
    assert [w["s"] for w in out] == list(range(len(PIECES)))  # every piece still plays its audio


def test_edited_or_mismatched_text_falls_back_to_its_own_phrases():
    assert shown(services.display_words(TEXT, None)) == TEXT
    assert shown(services.display_words(TEXT, [{"w": "ไม่ตรง", "s": 0, "e": 1, "p": 1}])) == TEXT


@pytest.mark.django_db
def test_verbatim_word_file_before_confirming(recorded, investigator, client_inv):  # noqa: F811
    url = reverse("interviews:transcript_docx", args=[recorded.pk])
    assert client_inv.get(url).status_code == 404  # nothing transcribed yet
    services.import_transcript(recorded, investigator, MULE)
    r = client_inv.get(url)
    assert (
        r.status_code == 200 and r["Content-Disposition"].startswith("attachment") and r["Content-Disposition"].endswith(".docx")
    )
    doc = Document(io.BytesIO(r.content))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
    first = recorded.segments.order_by("start").first().text
    assert "ข้อความถอดเสียงคำต่อคำ" in text and "ร่างโดย AI – รอตรวจ" in text and first in text
    assert AuditLog.objects.filter(user=investigator, action="export", detail__contains="คำต่อคำ").exists()


@pytest.mark.django_db
def test_review_page_shows_natural_text_and_a_copy_box(recorded, investigator, client_inv):  # noqa: F811
    services.import_transcript(recorded, investigator, MULE)
    seg = recorded.segments.order_by("start").first()
    seg.words = [{"w": w, "s": i, "e": i + 0.5, "p": 0.9} for i, w in enumerate(PIECES)]
    seg.text = seg.text_ai = TEXT
    seg.save()
    page = client_inv.get(reverse("interviews:review", args=[recorded.pk])).content.decode()
    li = page[page.index(f'id="seg-{seg.pk}"') :]
    para = li[li.index('<p class="seg-text">') : li.index("</p>")]
    assert re.sub(r"<[^>]+>", "", para).replace('<p class="seg-text">', "") == TEXT
    assert "คำพูดคำต่อคำ (Word)" in page and "ข้อความต่อเนื่องสำหรับคัดลอก" in page
