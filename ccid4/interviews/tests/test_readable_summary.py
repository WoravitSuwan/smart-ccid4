"""สรุปการสอบปากคำให้อ่านรู้เรื่อง: ไม่ตัดคำพูดของผู้แจ้งทิ้ง ไม่มีตัวอักษรขาด และดึงจำนวนเงินที่พูดเป็นคำได้.

ข้อความในเทสต์นี้แต่งขึ้นเอง (ข้อมูลสมมติ)
"""

import sys
import types

from ai import asr
from core.thai import numbers_in_speech, thai_words_to_int
from interviews import qa, summary


def seg(i, start, end, text, role="subject"):
    return qa.Seg(i, start, end, role, text)


def test_amounts_as_people_say_them():
    assert thai_words_to_int("หนึ่งแสนแปด", colloquial=True) == 180_000
    assert thai_words_to_int("สามพันห้า", colloquial=True) == 3_500
    assert thai_words_to_int("ล้านสอง", colloquial=True) == 1_200_000
    assert thai_words_to_int("สิบสอง", colloquial=True) == 12  # only พัน and up are shortened like this
    assert thai_words_to_int("หนึ่งแสนแปด") == 100_008  # the formal reading stays the default
    assert thai_words_to_int("ล้าน") == 1_000_000 and thai_words_to_int("สองล้าน") == 2_000_000
    assert {100_008, 180_000} <= numbers_in_speech("เติมอีกหนึ่งแสนแปด")  # a draft may write either


def test_money_said_in_words_is_found_and_other_numbers_are_not():
    said = ("วันแรกมีออเดอร์เข้ามาสามพัน แล้วก็มียอดเข้ามาอีกสองแสนกว่าบาท ต้องเอาเงินเข้าไปอีกหนึ่งแสนแปด "
            "ผมกดเลขสาม แล้วรออีกสองวัน")  # fmt: skip
    found = summary.spoken_amounts(said)
    assert [f.split(" (")[0] for f in found] == ["3,000 บาท", "200,000 กว่า บาท", "180,000 บาท"]
    facts = summary.key_facts(said)
    assert any(f.startswith("180,000 บาท") for f in facts["จำนวนเงิน"])


def test_stumbles_are_dropped_but_numbers_read_digit_by_digit_are_kept():
    assert summary.tidy("อยู่บ้าน อยู่บ้าน แล้วเขาทักมา") == "อยู่บ้าน แล้วเขาทักมา"
    assert summary.tidy("เราก็สำนอง สำนองจ่ายก่อน") == "เราก็สำนอง สำนองจ่ายก่อน"  # different words: untouched
    assert summary.tidy("สำนอง สำนองจ่ายก่อน") == "สำนองจ่ายก่อน"
    assert summary.tidy("เลขบัญชี สอง สอง ศูนย์ ศูนย์ หนึ่ง") == "เลขบัญชี สอง สอง ศูนย์ ศูนย์ หนึ่ง"


def test_a_complainant_quoting_a_question_is_still_the_complainant():
    quoting = "แล้วเขาก็ทักมาในแอปบอกว่าตอนนี้ร้านค้ากำลังเปิดรับสมัครร้านใหม่ สมัครง่ายมาก สนใจสมัครเลยไหม"
    assert len(quoting) > summary.QUESTION_MAX and summary.guess_speaker(quoting) == "S2"
    assert summary.guess_speaker("โอนไปทั้งหมดเท่าไหร่ครับ") == "S1"


def test_story_keeps_every_sentence_and_breaks_paragraphs_where_they_paused():
    segs = [
        seg(1, 0, 9, "ผมอยู่บ้าน อยู่บ้าน แล้วเขาทักมาในแอปชวนเปิดร้านค้าออนไลน์ บอกว่าไม่ต้องสต็อกสินค้า ขายได้กำไรดีครับ"),
        seg(2, 9.2, 20, "แล้วเขาก็บอกว่าตอนนี้ร้านค้ากำลังเปิดรับสมัครร้านใหม่ สมัครง่ายมาก สนใจสมัครเลยไหม ผมก็เลยสมัครไปครับ"),
        seg(3, 20.1, 31, "วันแรกมีออเดอร์เข้ามาสามพัน ผมก็โอนเงินเข้าไปก่อน แล้วระบบก็โอนคืนมาพร้อมกำไรครับ จะถอนเ�"),
        seg(4, 34.0, 45, "�ินก็ถอนไม่ได้ เขาบอกว่าต้องเติมเงินเข้าไปอีกหนึ่งแสนแปดก่อน ผมก็หาเงินมาเติมจนครบครับ"),
        seg(5, 45.5, 50, "แล้วก็ติดต่อเขาไม่ได้อีกเลยครับ"),
    ]
    story = summary.code_narrative([], segs)
    assert story.startswith("ข้าฯ ขอให้การว่า ") and "�" not in story
    assert "สนใจสมัครเลยไหม" in story  # the quoted question stays in the complainant's story
    assert "อยู่บ้าน อยู่บ้าน" not in story and "สามพัน" in story and "หนึ่งแสนแปด" in story
    paras = story.split("\n")
    assert len(paras) == 2 and paras[1].startswith("ินก็ถอนไม่ได้")  # new paragraph after the 3-second pause
    points = summary.code_points([], segs)
    assert any(p.startswith("ประเด็นจากคำบอกเล่า นาที 00:20:") and "สามพัน" in p for p in points)
    assert any("180,000 บาท" in p for p in points if p.startswith("จำนวนเงิน"))


def test_a_letter_split_between_two_segments_is_joined(settings, monkeypatch, tmp_path):
    pieces = {1: "จะถอน".encode(), 2: "เ".encode(), 3: "ง".encode()[:1], 4: "ง".encode()[1:] + "ินมาไม่ได้".encode()}

    class Tok:
        def token_to_id(self, t):
            return 50257

        def decode(self, ids):
            return b"".join(pieces[i] for i in ids).decode("utf-8", "replace")

    def segment(start, end, ids):
        return types.SimpleNamespace(start=start, end=end, tokens=ids, words=[], text=Tok().decode(ids))

    class Model:
        hf_tokenizer = Tok()

        def __init__(self, path, **kw):
            pass

        def transcribe(self, audio, **kw):
            return iter([segment(0, 2, [1, 2, 3]), segment(2, 4, [4])]), types.SimpleNamespace(duration=4.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(asr, "foreign_token_ids", lambda tok, key: [])
    settings.ASR_BACKEND, settings.ASR_MODEL, settings.ASR_DEVICE = "whisper", str(tmp_path), "cpu"
    out = asr.transcribe_audio([0.0] * 16000)
    assert [o["text"] for o in out] == ["จะถอนเงินมาไม่ได้"] and (out[0]["start"], out[0]["end"]) == (0, 4)
