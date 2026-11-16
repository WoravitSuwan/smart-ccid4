"""Build documents/library/*.docx from documents/library_src.py (run after editing the wording)."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

from documents.library_src import TEMPLATES

LIBRARY = Path(__file__).resolve().parents[2] / "library"
SIZE = 16


def thai_font(run, size=SIZE, bold=False):
    run.font.name = settings.DOC_FONT
    run.font.size = Pt(size)
    run.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), settings.DOC_FONT)
    rpr.append(rpr.makeelement(qn("w:szCs"), {qn("w:val"): str(size * 2)}))
    if bold:
        rpr.append(rpr.makeelement(qn("w:bCs"), {}))
    return run


def para(container, text="", align=None, size=SIZE, bold=False, indent=0, after=0):
    p = container.add_paragraph()
    p.alignment = align
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Pt(0), Pt(after)
    if indent:
        pf.first_line_indent = Mm(indent)
    if text:
        thai_font(p.add_run(text), size, bold)
    return p


def new_doc():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = Mm(25), Mm(20), Mm(30), Mm(20)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = settings.DOC_FONT, Pt(SIZE)
    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    thai_font(footer.add_run("CCID4 · {หน่วย.ชื่อ}"), 10)
    return doc


def build(key, blocks) -> Document:
    doc = new_doc()
    for block in blocks:
        kind = block[0]
        if kind == "head":
            para(doc, block[1], WD_ALIGN_PARAGRAPH.CENTER, 20, True, after=8)
        elif kind == "c":
            para(doc, block[1], WD_ALIGN_PARAGRAPH.CENTER)
        elif kind == "r":
            para(doc, block[1], WD_ALIGN_PARAGRAPH.RIGHT)
        elif kind == "l":
            para(doc, block[1], after=2)
        elif kind == "p":
            para(doc, block[1], WD_ALIGN_PARAGRAPH.THAI_JUSTIFY, indent=25, after=4)
        elif kind == "gap":
            para(doc)
        elif kind == "table":
            headers, cells = block[1], block[2]
            table = doc.add_table(rows=2, cols=len(headers))
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            for cell, text in zip(table.rows[0].cells, headers, strict=True):
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                thai_font(cell.paragraphs[0].add_run(text), SIZE, True)
            for cell, text in zip(table.rows[1].cells, cells, strict=True):
                thai_font(cell.paragraphs[0].add_run(text))
            para(doc)
        elif kind == "sign":
            _, role, name, position = block
            para(doc)
            para(doc, f"(ลงชื่อ)........................................{role}", WD_ALIGN_PARAGRAPH.RIGHT)
            if name:
                para(doc, f"( {name} )", WD_ALIGN_PARAGRAPH.RIGHT)
            if position:
                para(doc, position, WD_ALIGN_PARAGRAPH.RIGHT)
        else:
            raise ValueError(f"{key}: ไม่รู้จักชนิด {kind}")
    return doc


class Command(BaseCommand):
    help = "สร้างไฟล์เทมเพลตเอกสาร .docx ใน documents/library จาก library_src.py"

    def handle(self, *args, **opts):
        LIBRARY.mkdir(exist_ok=True)
        for key, blocks in TEMPLATES.items():
            doc = build(key, blocks)
            doc.core_properties.author = "CCID4"
            doc.core_properties.title = key
            doc.save(LIBRARY / f"{key}.docx")
        self.stdout.write(f"สร้างเทมเพลต {len(TEMPLATES)} ไฟล์ใน {LIBRARY}")
