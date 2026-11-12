"""Word documents (python-docx) and PDF conversion (LibreOffice, run locally).

Government documents use Thai digits and BE dates throughout. Thai is a
complex script in Word, so fonts and sizes are set on the complex-script
properties too, otherwise Word falls back to another font.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

from . import config
from .thai import format_date, format_time, mmss, to_thai_digits

FONT_SIZE = 16


def _font(run, size: int = FONT_SIZE, bold: bool = False):
    run.font.name = config.DOC_FONT
    run.font.size = Pt(size)
    run.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), config.DOC_FONT)
    szcs = rpr.makeelement(qn("w:szCs"), {qn("w:val"): str(size * 2)})
    rpr.append(szcs)
    if bold:
        rpr.append(rpr.makeelement(qn("w:bCs"), {}))
    return run


def _para(doc, text: str = "", align=None, size=FONT_SIZE, bold=False, indent_mm: float = 0, space_after=0):
    p = doc.add_paragraph()
    if align:
        p.alignment = align
    pf = p.paragraph_format
    pf.space_after = Pt(space_after)
    pf.space_before = Pt(0)
    if indent_mm:
        pf.first_line_indent = Mm(indent_mm)
    if text:
        _font(p.add_run(text), size, bold)
    return p


def _new_doc() -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin, sec.bottom_margin = Mm(25), Mm(20)
    sec.left_margin, sec.right_margin = Mm(30), Mm(20)
    style = doc.styles["Normal"]
    style.font.name = config.DOC_FONT
    style.font.size = Pt(FONT_SIZE)
    return doc


def _doc_date(d: date | datetime) -> str:
    return format_date(d, "doc")


def _sig(doc, label: str, name: str = "", position: str = ""):
    _para(doc, "")
    _para(doc, f"(ลงชื่อ)................................................{label}", WD_ALIGN_PARAGRAPH.RIGHT)
    if name:
        _para(doc, f"( {name} )", WD_ALIGN_PARAGRAPH.RIGHT)
    if position:
        _para(doc, position, WD_ALIGN_PARAGRAPH.RIGHT)


# ---------------------------------------------------------------- statement
@dataclass
class StatementData:
    person_role: str
    person_name: str
    case_no: str
    charge: str
    place: str
    when: datetime
    investigator: str
    investigator_position: str
    pairs: list[tuple[str, str, float, float]]
    audio_sha256: str = ""
    audio_duration: float = 0
    include_times: bool = True
    draft: bool = False


def statement_docx(data: StatementData, out: Path) -> Path:
    doc = _new_doc()
    if data.draft:
        _para(doc, "ร่าง — ยังไม่ได้ยืนยันโดยพนักงานสอบสวน", WD_ALIGN_PARAGRAPH.CENTER, 14, True)
    _para(doc, f"บันทึกคำให้การของ{data.person_role}", WD_ALIGN_PARAGRAPH.CENTER, 20, True, space_after=6)
    _para(doc, f"คดีอาญาที่ {to_thai_digits(data.case_no)}", WD_ALIGN_PARAGRAPH.RIGHT)
    _para(doc, f"สถานที่สอบสวน {data.place}", WD_ALIGN_PARAGRAPH.RIGHT)
    _para(doc, f"วันที่ {_doc_date(data.when)} เวลา {format_time(data.when, doc=True)}", WD_ALIGN_PARAGRAPH.RIGHT, space_after=6)
    _para(doc, f"ข้อหา {data.charge}", indent_mm=12)
    _para(doc, f"ข้าฯ {data.person_name} ขอให้ถ้อยคำต่อพนักงานสอบสวน ดังนี้", indent_mm=12, space_after=6)
    for q, a, start, end in data.pairs:
        _para(doc, f"ถาม {to_thai_digits(q)}", indent_mm=12)
        p = _para(doc, f"ตอบ {to_thai_digits(a)}", indent_mm=12)
        if data.include_times:
            _font(p.add_run(f"  [เสียงนาทีที่ {to_thai_digits(mmss(start))}–{to_thai_digits(mmss(end))}]"), 12)
        p.paragraph_format.space_after = Pt(4)
    _para(doc, "อ่านให้ฟังแล้ว รับว่าถูกต้อง จึงลงลายมือชื่อไว้เป็นหลักฐาน", indent_mm=12)
    _sig(doc, "ผู้ให้ถ้อยคำ")
    _sig(doc, "พนักงานสอบสวน", data.investigator, data.investigator_position)
    _sig(doc, "ผู้บันทึก")
    if data.audio_sha256:
        _para(doc, "")
        _para(doc, f"การสอบปากคำครั้งนี้บันทึกเสียงไว้ ความยาว {to_thai_digits(mmss(data.audio_duration))} "
                   f"ค่าตรวจสอบไฟล์เสียงต้นฉบับ (SHA-256) {data.audio_sha256}", size=12)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


# ---------------------------------------------------------------- summons
@dataclass
class SummonsItem:
    case_no: str
    charge: str
    name: str
    address: str
    postcode: str
    appointment: date
    time: str
    place: str
    signer: str
    signer_position: str
    signer_phone: str


def summons_docx(items: list[SummonsItem], issue_date: date, out: Path) -> Path:
    doc = _new_doc()
    for i, it in enumerate(items):
        if i:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        _para(doc, "[ตราครุฑจากแบบฟอร์มของหน่วย]", WD_ALIGN_PARAGRAPH.CENTER, 12)
        _para(doc, "หมายเรียกผู้กล่าวหา/พยานบุคคล", WD_ALIGN_PARAGRAPH.CENTER, 20, True, space_after=6)
        _para(doc, f"ที่ {config.UNIT_SHORT}", WD_ALIGN_PARAGRAPH.RIGHT)
        _para(doc, f"วันที่ {_doc_date(issue_date)}", WD_ALIGN_PARAGRAPH.RIGHT, space_after=6)
        _para(doc, f"ถึง {it.name} อยู่ {to_thai_digits(it.address)} {to_thai_digits(it.postcode)}".strip())
        _para(doc, f"ด้วยท่านเป็นผู้กล่าวหาหรือพยานในคดีอาญาที่ {to_thai_digits(it.case_no)} ข้อหา {it.charge} "
                   "ซึ่งพนักงานสอบสวนมีความจำเป็นต้องสอบปากคำท่าน", indent_mm=12)
        _para(doc, f"จึงให้ท่านไปพบพนักงานสอบสวน ณ {it.place} ในวันที่ {_doc_date(it.appointment)} "
                   f"เวลา {format_time(it.time, doc=True)}", indent_mm=12)
        _para(doc, "หากไม่สามารถมาตามวันเวลาดังกล่าวได้ โปรดติดต่อพนักงานสอบสวนล่วงหน้า", indent_mm=12)
        _sig(doc, "", it.signer, it.signer_position)
        _para(doc, f"พนักงานสอบสวน โทร. {to_thai_digits(it.signer_phone)}", WD_ALIGN_PARAGRAPH.RIGHT)
        _para(doc, "- - - - - - - - - - - - - - - ตัดตามรอยประ - - - - - - - - - - - - - - -", WD_ALIGN_PARAGRAPH.CENTER, 12)
        _para(doc, "ใบรับหมาย", WD_ALIGN_PARAGRAPH.CENTER, 16, True)
        _para(doc, f"ข้าพเจ้า.......................................ได้รับหมายเรียกคดีอาญาที่ {to_thai_digits(it.case_no)} "
                   "แล้ว เมื่อวันที่..........................", indent_mm=12)
        _para(doc, "(ลงชื่อ)................................................ผู้รับหมาย", WD_ALIGN_PARAGRAPH.RIGHT)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


def envelopes_docx(items: list[SummonsItem], out: Path) -> Path:
    """DL envelopes (220 x 110 mm), one per page."""
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Mm(220), Mm(110)
    sec.top_margin = sec.bottom_margin = Mm(10)
    sec.left_margin = sec.right_margin = Mm(12)
    for i, it in enumerate(items):
        if i:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        _para(doc, config.UNIT_NAME, size=12)
        _para(doc, "")
        p = _para(doc, "กรุณาส่ง", size=14)
        p.paragraph_format.left_indent = Mm(95)
        for line in (it.name, to_thai_digits(it.address), f"รหัสไปรษณีย์ {to_thai_digits(it.postcode) or '[ไม่มีข้อมูล]'}"):
            q = _para(doc, line, size=18, bold=True)
            q.paragraph_format.left_indent = Mm(95)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


# ---------------------------------------------------------------- PDF
def pdf_available() -> bool:
    return shutil.which(config.SOFFICE_BIN) is not None


def to_pdf(docx_path: Path) -> Path:
    """Convert with LibreOffice on this machine. Raises RuntimeError if unavailable."""
    if not pdf_available():
        raise RuntimeError("ไม่พบ LibreOffice บนเครื่องนี้ จึงสร้าง PDF ไม่ได้")
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [config.SOFFICE_BIN, f"-env:UserInstallation=file://{profile}", "--headless",
             "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path)],
            check=True, capture_output=True, timeout=600,
        )
    pdf = docx_path.with_suffix(".pdf")
    if not pdf.exists():
        raise RuntimeError("แปลงเป็น PDF ไม่สำเร็จ")
    return pdf
