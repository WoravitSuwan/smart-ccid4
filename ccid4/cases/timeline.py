"""Timeline คดี (B10): every dated event of a case in order."""

from dataclasses import dataclass
from datetime import date, datetime, time
from io import BytesIO

from django.conf import settings
from django.utils import timezone

from core import thai


@dataclass
class Event:
    when: datetime
    all_day: bool
    kind: str  # report | statement | arrest | detention | letter | reply | task | record
    title: str
    detail: str = ""

    @property
    def label(self) -> str:
        return thai.format_date(self.when.date()) if self.all_day else thai.format_datetime(self.when)


KIND_LABELS = {
    "report": "รับแจ้ง",
    "record": "บันทึกประจำวัน",
    "incident": "เกิดเหตุ",
    "statement": "สอบปากคำ",
    "arrest": "จับกุม",
    "detention": "ฝากขัง/ผัดฟ้อง",
    "letter": "ออกหนังสือ",
    "reply": "ได้รับหนังสือตอบ",
    "task": "บันทึกพนักงานสอบสวน",
}


def _day(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


def events(case) -> list[Event]:
    ev: list[Event] = []
    loc = timezone.localtime
    if case.incident_from:
        ev.append(Event(loc(case.incident_from), False, "incident", "เกิดเหตุ", case.location))
    ev.append(Event(loc(case.reported_at), False, "report", "รับแจ้งความ", case.offense))
    if case.dr_complaint_at:
        ev.append(
            Event(loc(case.dr_complaint_at), False, "record", f"ลงบันทึกประจำวันรับคำร้องทุกข์ ข้อ {case.dr_complaint_item}".strip())
        )
    if case.dr_caseno_at:
        ev.append(Event(loc(case.dr_caseno_at), False, "record", f"ตัดเลขคดีอาญา {case.case_no}".strip()))
    for p in case.parties.select_related("person").prefetch_related("statements", "detentions"):
        name = p.person.full_name
        for st in p.statements.all():
            ev.append(
                Event(
                    loc(st.created_at),
                    False,
                    "statement",
                    f"คำให้การ{p.get_role_display()} {name} ฉบับที่ {st.version}",
                    st.get_source_display(),
                )
            )
        if p.arrested_at:
            ev.append(Event(loc(p.arrested_at), False, "arrest", f"จับกุม/มอบตัว {name}", p.custody_place))
        if p.arrived_at:
            ev.append(Event(loc(p.arrived_at), False, "arrest", f"{name} ถึงที่ทำการพนักงานสอบสวน"))
        for i, d in enumerate(p.detentions.all(), start=1):
            ev.append(
                Event(
                    _day(d.start_date),
                    True,
                    "detention",
                    f"{d.get_kind_display()}ผัดที่ {i} {name}",
                    f"{thai.format_date(d.start_date)} – {thai.format_date(d.end_date)} ({d.days} วัน) {d.court}".strip(),
                )
            )
    for lt in case.letters.all():
        if lt.issued_at:
            ev.append(Event(_day(lt.issued_at), True, "letter", f"ออกหนังสือ {lt.number}: {lt.subject}", f"เรียน {lt.recipient}"))
        if lt.replied_at:
            ev.append(Event(_day(lt.replied_at), True, "reply", f"ได้รับหนังสือตอบ {lt.number}", lt.reply_note))
    for t in case.tasks.filter(done_at__isnull=False).exclude(source="auto"):
        ev.append(Event(loc(t.done_at), False, "task", t.title, t.reason))
    ev.sort(key=lambda e: (e.when.date(), e.all_day, e.when))  # a dated-only event goes after timed ones that day
    return ev


def to_docx(case, items: list[Event]) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = settings.DOC_FONT
    style.font.size = Pt(16)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rFonts")
    if rfonts is not None:
        rfonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}cs", settings.DOC_FONT)
    style.element.rPr.append(
        style.element.makeelement(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}szCs",
            {"{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val": "32"},
        )
    )
    for name in ("Heading 1", "Title"):
        doc.styles[name].font.name = settings.DOC_FONT
    doc.add_heading(f"Timeline · {case.label}", level=1)
    doc.add_paragraph(f"{settings.UNIT_NAME} · ฐานความผิด {case.offense or '-'}")
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ("วันเวลา", "เหตุการณ์", "รายละเอียด"), strict=True):
        cell.text = text
    for e in items:
        row = table.add_row().cells
        row[0].text, row[1].text, row[2].text = e.label, e.title, e.detail or ""
    doc.add_paragraph(f"พิมพ์เมื่อ {thai.format_datetime(timezone.localtime())}")
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
