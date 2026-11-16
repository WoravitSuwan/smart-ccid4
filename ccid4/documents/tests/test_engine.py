from io import BytesIO
from pathlib import Path

from docx import Document

from documents import engine

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data" / "summons_template_sanitized.docx"


def make_docx(build) -> bytes:
    doc = Document()
    build(doc)
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def text_of(data: bytes) -> str:
    doc = Document(BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(parts)


def test_placeholder_split_over_runs_keeps_first_run_format():
    def build(doc):
        p = doc.add_paragraph()
        p.add_run("เรียน ")
        r = p.add_run("{ชื่อ")
        r.bold = True
        p.add_run("ผู้แจ้ง}")
        p.add_run(" ที่อยู่ {ที่อยู่} ครั้งที่ {ครั้ง}")

    out, missing = engine.fill(make_docx(build), {"ชื่อผู้แจ้ง": "นายสมมติ ทดสอบ", "ที่อยู่": "99/1 ม.2", "ครั้ง": 3})
    doc = Document(BytesIO(out))
    p = doc.paragraphs[0]
    assert p.text == "เรียน นายสมมติ ทดสอบ ที่อยู่ ๙๙/๑ ม.๒ ครั้งที่ ๓"
    assert p.runs[1].bold and p.runs[1].text == "นายสมมติ ทดสอบ"
    assert missing == []


def test_missing_values_are_reported_and_dotted():
    out, missing = engine.fill(
        make_docx(lambda d: d.add_paragraph("{a} {คดี.เลข} {ไม่มี}")), {"a": "x", "คดี": {"เลข": "12/2569"}}, thai_digits=False
    )
    assert text_of(out).startswith("x 12/2569 ……")
    assert missing == ["ไม่มี"]


def test_arabic_digits_option_and_line_breaks():
    out, _ = engine.fill(make_docx(lambda d: d.add_paragraph("{t}")), {"t": "บรรทัด 1\nบรรทัด 2"}, thai_digits=False)
    assert "บรรทัด 1\nบรรทัด 2" in text_of(out)


def test_table_rows_repeat_for_lists():
    def build(doc):
        t = doc.add_table(rows=2, cols=3)
        t.rows[0].cells[0].text = "ลำดับ"
        t.rows[1].cells[0].text = "{ทรัพย์.ลำดับ}"
        t.rows[1].cells[1].text = "{ทรัพย์.ชื่อ}"
        t.rows[1].cells[2].text = "{ทรัพย์.มูลค่า}"
        doc.add_paragraph("รวม {รวม} บาท")

    items = [{"ชื่อ": "โทรศัพท์", "มูลค่า": "12,000"}, {"ชื่อ": "กระเป๋า", "มูลค่า": "500"}]
    out, missing = engine.fill(make_docx(build), {"ทรัพย์": items, "รวม": "12,500"})
    txt = text_of(out)
    assert "๑ | โทรศัพท์ | ๑๒,๐๐๐" in txt and "๒ | กระเป๋า | ๕๐๐" in txt and "รวม ๑๒,๕๐๐ บาท" in txt
    assert missing == []


def test_empty_list_keeps_one_dash_row():
    def build(doc):
        t = doc.add_table(rows=1, cols=2)
        t.rows[0].cells[0].text = "{ทรัพย์.ชื่อ}"
        t.rows[0].cells[1].text = "{ทรัพย์.มูลค่า}"

    out, _ = engine.fill(make_docx(build), {"ทรัพย์": []})
    assert "- | -" in text_of(out)


def test_sample_summons_template_from_the_unit():
    data = SAMPLE.read_bytes()
    names = engine.find_placeholders(data)
    assert {"วันปัจจุบัน", "เดือนปัจจุบัน", "ปีปัจจุบัน", "ชื่อผู้แจ้ง", "ที่อยู่"} <= set(names)
    values = {n: f"ค่า-{n}" for n in names} | {"วันปัจจุบัน": "16"}
    out, missing = engine.fill(data, values)
    txt = text_of(out)
    assert "{" not in txt.replace("{}", "") and missing == []
    assert "๑๖" in txt and "ค่า-ชื่อผู้แจ้ง" in txt


def test_headers_are_filled():
    def build(doc):
        doc.sections[0].header.paragraphs[0].text = "คดี {เลขคดี}"

    out, _ = engine.fill(make_docx(build), {"เลขคดี": "5/2569"})
    assert Document(BytesIO(out)).sections[0].header.paragraphs[0].text == "คดี ๕/๒๕๖๙"
