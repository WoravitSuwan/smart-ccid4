"""รายงานประจำเดือน (B7): Excel และ PDF จากข้อมูลชุดเดียวกับแดชบอร์ดหน่วยงาน."""

from __future__ import annotations

import io

from django.conf import settings
from django.utils import timezone

from core import thai


def excel(data: dict, user) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "สรุป"
    rows = [
        ("รายงานประจำเดือน", data["month_label"]),
        ("คดีทั้งหมดในขอบเขต", data["total"]),
        ("คดีที่ยังไม่เสร็จ", data["open"]),
        ("รับแจ้งในเดือน", data["received_month"]),
        ("กำหนดเวลาที่เลยแล้ว (รายการ)", data["overdue"]),
        ("คดีที่มีกำหนดเลยแล้ว", data["overdue_cases"]),
        ("มูลค่าความเสียหาย คดีที่รับแจ้งในเดือน (บาท)", float(data["damage_month"])),
        ("มูลค่าความเสียหาย ทุกคดีในขอบเขต (บาท)", float(data["damage_all"])),
    ]
    for r in rows:
        ws.append(r)
    ws.append([])
    ws.append(["ประเภทคดี (รับแจ้งในเดือน)", "จำนวน"])
    for k, v in data["by_type"]:
        ws.append([k, v])

    def sheet(title, header, body, widths):
        s = wb.create_sheet(title)
        s.append(header)
        for c in s[1]:
            c.font = Font(bold=True)
        for row in body:
            s.append(row)
        for col, w in zip("ABCDEFGH", widths, strict=False):
            s.column_dimensions[col].width = w

    sheet(
        "รายพนักงานสอบสวน",
        ["พนักงานสอบสวน", "คดีที่ยังไม่เสร็จ", "เสร็จสิ้น", "รับแจ้งในเดือน", "เลยกำหนด", "ครบใน 7 วัน"],
        [[o.name, o.open, o.closed, o.received, o.overdue, o.week] for o in data["officers"]],
        (30, 16, 12, 16, 12, 14),
    )
    sheet("ฐานความผิด", ["ฐานความผิด", f"จำนวนคดี ({data['offense_scope']})"], [list(x) for x in data["offenses"]], (60, 20))
    sheet("รายเดือน 12 เดือน", ["เดือน", "รับแจ้ง (คดี)"], [[label, n] for label, n, _ in data["monthly"]], (16, 16))
    sheet("บัญชีม้าที่พบซ้ำ", ["เลขบัญชี (ปิดบัง)", "พบในกี่คดี", "ยอดรับรวม (บาท)", "ธนาคาร", "สถานะ"],
          [[m["masked"], m["cases"], float(m["received"]), m["banks"], m["status"]] for m in data["mules"]], (20, 12, 18, 24, 40))  # fmt: skip
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 46, 20
    ws.append([])
    ws.append([f"ส่งออกโดย {user.display_name} · {thai.format_datetime(timezone.localtime())} · CCID4"])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def pdf(data: dict, user) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    from core.convert import docx_to_pdf
    from documents.management.commands.build_doc_templates import para, thai_font

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = Mm(20), Mm(18), Mm(22), Mm(18)
    doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = settings.DOC_FONT, Pt(14)
    foot = sec.footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    thai_font(foot.add_run(f"ส่งออกโดย {user.display_name} · {thai.format_datetime(timezone.localtime())} · CCID4"), 9)
    para(doc, f"รายงานประจำเดือน {data['month_label']}", WD_ALIGN_PARAGRAPH.CENTER, 20, True, after=2)
    para(doc, settings.UNIT_NAME, WD_ALIGN_PARAGRAPH.CENTER, 14, after=8)

    def table(header, body, widths=None):
        t = doc.add_table(rows=1, cols=len(header))
        t.style = "Table Grid"
        for cell, text in zip(t.rows[0].cells, header, strict=True):
            thai_font(cell.paragraphs[0].add_run(str(text)), 13, True)
        for row in body:
            cells = t.add_row().cells
            for cell, text in zip(cells, row, strict=True):
                thai_font(cell.paragraphs[0].add_run(str(text)), 13)
        para(doc)

    def money(v):
        return f"{v:,.2f}"

    para(doc, "๑. สรุป", size=16, bold=True, after=2)
    table(["รายการ", "จำนวน"], [
        ("คดีทั้งหมดในขอบเขต", data["total"]), ("คดีที่ยังไม่เสร็จ", data["open"]), ("รับแจ้งในเดือน", data["received_month"]),
        ("กำหนดเวลาที่เลยแล้ว (รายการ / คดี)", f"{data['overdue']} / {data['overdue_cases']}"),
        ("มูลค่าความเสียหาย คดีที่รับแจ้งในเดือน (บาท)", money(data["damage_month"])),
        ("มูลค่าความเสียหาย ทุกคดีในขอบเขต (บาท)", money(data["damage_all"])),
    ])  # fmt: skip
    para(doc, "๒. รายพนักงานสอบสวน", size=16, bold=True, after=2)
    table(["พนักงานสอบสวน", "ยังไม่เสร็จ", "เสร็จสิ้น", "รับแจ้งในเดือน", "เลยกำหนด", "ครบใน 7 วัน"],
          [(o.name, o.open, o.closed, o.received, o.overdue, o.week) for o in data["officers"]] or [("-", "", "", "", "", "")])  # fmt: skip
    para(doc, f"๓. ฐานความผิด ({data['offense_scope']})", size=16, bold=True, after=2)
    table(["ฐานความผิด", "จำนวนคดี"], data["offenses"] or [("-", "")])
    para(doc, "๔. คดีรับแจ้งรายเดือน (12 เดือน)", size=16, bold=True, after=2)
    table(["เดือน", "คดี"], [(label, n) for label, n, _ in data["monthly"]])
    para(doc, "๕. บัญชีม้าที่พบซ้ำหลายคดี", size=16, bold=True, after=2)
    table(["เลขบัญชี (ปิดบัง)", "พบในคดี", "ยอดรับรวม (บาท)", "ธนาคาร"],
          [(m["masked"], m["cases"], money(m["received"]), m["banks"]) for m in data["mules"]] or [("-", "", "", "")])  # fmt: skip
    out = io.BytesIO()
    doc.save(out)
    return docx_to_pdf(out.getvalue())
