from datetime import date, datetime

from app.interview import Seg, build_pairs, check_rights, confirm_blockers, number_flags, to_official_answer
from app.thai import (add_minutes, format_date, mask_account, mask_citizen_id, numbers_in_speech, parse_be_date,
                      thai_words_to_int, to_thai_digits)
from app.transcribe import parse_transcript


def test_thai_dates_and_digits():
    d = date(2026, 11, 25)
    assert format_date(d) == "25 พ.ย. 2569"
    assert format_date(d, "doc") == "๒๕ เดือน พฤศจิกายน พ.ศ. ๒๕๖๙"
    assert to_thai_digits("09.00") == "๐๙.๐๐"
    assert parse_be_date("25/11/2569") == d
    assert parse_be_date("๒๕/๑๑/๒๕๖๙") == d
    assert add_minutes("09:00", 30 * 3) == "10:30"


def test_masking():
    assert mask_citizen_id("3000000000456") == "3-xxxx-xxxxx-45-6"
    assert mask_account("1234567890") == "xxx-x-x6789-x"


def test_thai_number_words():
    assert thai_words_to_int("สามพัน") == 3000
    assert thai_words_to_int("สองแสนแปดหมื่นแปดพัน") == 288000
    assert thai_words_to_int("หนึ่งร้อยยี่สิบเอ็ด") == 121
    assert thai_words_to_int("ห้าล้านสองแสน") == 5_200_000
    assert numbers_in_speech("ค่าเช่าสามพันบาท โอน 1,500 บาท") == {3000, 1500}


SEGS = [
    Seg(31, 40, "investigator", "ท่านถูกกล่าวหาว่าฉ้อโกงประชาชน"),
    Seg(45, 60, "investigator", "ท่านมีสิทธิที่จะให้การหรือไม่ให้การก็ได้ ถ้าให้การ ถ้อยคำนั้นอาจใช้เป็นพยานหลักฐาน"),
    Seg(64, 66, "subject", "ครับ เข้าใจครับ"),
    Seg(70, 74, "lawyer", "ทนายความขอแถลงว่าผู้ต้องหาประสงค์จะให้การ"),
    Seg(820, 826, "investigator", "มีคนอื่นใช้บัญชีนี้ไหมครับ"),
    Seg(828, 845, "subject", "ผมให้สมุดกับบัตรเอทีเอ็ม เขาบอกว่าจะให้ค่าเช่าสามพันบาทครับ"),
    Seg(871, 874, "investigator", "ได้รับเงินจริงไหม"),
    Seg(876, 880, "subject", "ได้ครับ"),
    Seg(881, 884, "subject", "โอนมาครั้งเดียว"),
]


def test_pairs_keep_words_and_times():
    pairs = build_pairs(SEGS)
    assert [p.question for p in pairs][-2:] == ["มีคนอื่นใช้บัญชีนี้ไหม", "ได้รับเงินจริงไหม"]
    assert pairs[-2].answer == "ข้าฯ ให้สมุดกับบัตรเอทีเอ็ม เขาบอกว่าจะให้ค่าเช่าสามพันบาท"
    assert (pairs[-2].start, pairs[-2].end) == (820, 845)
    assert pairs[-1].answer == "ข้าฯ ได้ โอนมาครั้งเดียว"
    assert all("ทนายความ" not in p.answer for p in pairs)


def test_official_answer():
    assert to_official_answer("ดิฉันไม่รู้จักค่ะ") == "ข้าฯ ไม่รู้จัก"
    assert to_official_answer("ไม่ทราบครับ") == "ข้าฯ ไม่ทราบ"


def test_number_flags():
    flags = number_flags("ข้าฯ ตกลงค่าเช่า 5,000 บาท", "", SEGS, 820, 845)
    assert len(flags) == 1 and flags[0].value == 5000 and 3000 in flags[0].heard_nearby
    assert number_flags("ข้าฯ ตกลงค่าเช่า 3,000 บาท บัญชี xxx-x-x1234-x", "", SEGS, 820, 845) == []


def test_rights_and_blockers():
    rights = check_rights(SEGS)
    assert [r.ok for r in rights] == [True, True, False]
    assert rights[0].when == "00:45"
    assert confirm_blockers([True, True], rights, manual_rights_ok=False, is_suspect=True)
    assert confirm_blockers([True, True], rights, manual_rights_ok=True, is_suspect=True) == []
    assert confirm_blockers([True, False], rights, manual_rights_ok=True, is_suspect=True) == ["ยังไม่ได้ตรวจ 1 คู่"]
    assert confirm_blockers([True], rights, manual_rights_ok=False, is_suspect=False) == []


def test_parse_transcripts():
    lines = parse_transcript("[00:45] S1: ท่านมีสิทธิ\n01:04 S2: ครับ\nต่อบรรทัด")
    assert lines[0]["start"] == 45 and lines[0]["end"] == 64 and lines[1]["text"] == "ครับ ต่อบรรทัด"
    srt = parse_transcript("1\n00:00:01,000 --> 00:00:04,500\nS1: สวัสดี\n\n2\n00:00:05,000 --> 00:00:07,000\n<v S2>ครับ")
    assert [(s["speaker"], s["start"], s["end"]) for s in srt] == [("S1", 1.0, 4.5), ("S2", 5.0, 7.0)]
    js = parse_transcript('[{"start": 1, "end": 2, "text": "ก", "speaker": "A"}]')
    assert js == [{"start": 1.0, "end": 2.0, "text": "ก", "speaker": "A"}]
