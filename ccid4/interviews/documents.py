"""คำให้การจากเสียง → Word/PDF + ชุดหลักฐาน ZIP.

แบบฟอร์มชั่วคราวสร้างโดย build_template() (interviews/library/statement_voice.docx). เมื่อได้แบบฟอร์มจริงของหน่วย
อัปโหลดแทนได้ในหน้าตั้งค่า โดยใช้ตัวแทนตาม PLACEHOLDERS (ไม่ต้องแก้โค้ด).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from audit.services import log
from core import thai
from core.convert import docx_to_pdf, soffice_available
from documents import engine

from .models import InterviewDocument, InterviewSession, Participant

LIBRARY = Path(__file__).resolve().parent / "library"
DEFAULT_TEMPLATE = LIBRARY / "statement_voice.docx"
CUSTOM_TEMPLATE = "interviews/template/statement_voice.docx"  # uploaded in settings (media)

PLACEHOLDERS = {
    "ฐานะ": "ฐานะผู้ให้การ (ผู้ต้องหา/ผู้กล่าวหา/พยาน)",
    "ผู้ให้การ": "ชื่อ-นามสกุลผู้ให้การ",
    "อายุ": "อายุผู้ให้การ",
    "ที่อยู่": "ที่อยู่ผู้ให้การ",
    "เลขคดี": "เลขคดีอาญาหรือเลขรับแจ้ง",
    "ข้อหา": "ฐานความผิด",
    "สถานที่": "สถานที่สอบปากคำ",
    "วันที่": "วันที่สอบปากคำ (แบบราชการ)",
    "เวลาเริ่ม": "เวลาเริ่มบันทึกเสียง",
    "ย่อหน้าแจ้งสิทธิ": "ข้อความแจ้งสิทธิ (เฉพาะผู้ต้องหา ไม่ใช่ผู้ต้องหา = ว่าง)",
    "เรื่องเล่า": "คำให้การแบบบรรยาย (เล่าเรื่อง) — ใช้เมื่อเลือกรูปแบบบรรยาย แบบถาม–ตอบ = ว่าง",
    "qa.ถาม / qa.ตอบ / qa.เวลา / qa.ลำดับ": "บล็อกถาม–ตอบ: วางในแถวตาราง 1 แถว ระบบทำซ้ำต่อคู่ (แบบบรรยาย = ตัดแถวออก)",
    "บล็อกล่าม": "ข้อความรับรองของล่าม (ไม่มีล่าม = ว่าง)",
    "พงส": "ยศ ชื่อ พนักงานสอบสวน",
    "ตำแหน่ง": "ตำแหน่งพนักงานสอบสวน",
    "ทนาย": "ทนายความหรือผู้ไว้วางใจ (ไม่มี = ว่าง)",
    "ล่าม": "ล่าม (ไม่มี = ว่าง)",
    "ผู้บันทึก": "ผู้บันทึก (ไม่ระบุ = พนักงานสอบสวน)",
    "รหัสเอกสาร": "รหัสเอกสาร",
    "sha256": "SHA-256 ของไฟล์เสียงต้นฉบับ",
}
OPTIONAL = {"ย่อหน้าแจ้งสิทธิ", "บล็อกล่าม", "ทนาย", "ล่าม", "อายุ", "ที่อยู่", "เรื่องเล่า"}


def build_template() -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    from documents.management.commands.build_doc_templates import para, thai_font

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = Mm(25), Mm(20), Mm(30), Mm(20)
    sec.different_first_page_header_footer = True
    doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = settings.DOC_FONT, Pt(16)
    head = sec.header.paragraphs[0]
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    thai_font(head.add_run("ใบต่อคำให้การ ของ {ผู้ให้การ}"), 16, True)
    for footer in (sec.footer, sec.first_page_footer):
        f = footer.paragraphs[0]
        f.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        thai_font(f.add_run("รหัสเอกสาร {รหัสเอกสาร} · SHA-256 ไฟล์เสียง {sha256}"), 9)
    para(doc, "บันทึกคำให้การ{ฐานะ}", WD_ALIGN_PARAGRAPH.CENTER, 20, True, after=8)
    para(doc, "คดี {เลขคดี}", WD_ALIGN_PARAGRAPH.RIGHT)
    para(doc, "สถานที่สอบสวน {สถานที่}", WD_ALIGN_PARAGRAPH.RIGHT)
    para(doc, "วันที่ {วันที่} เวลา {เวลาเริ่ม}", WD_ALIGN_PARAGRAPH.RIGHT, after=6)
    para(doc, "ข้อหา {ข้อหา}", indent=12)
    para(
        doc,
        "ข้าฯ {ผู้ให้การ} อายุ {อายุ} ปี ที่อยู่ {ที่อยู่} ขอให้ถ้อยคำต่อพนักงานสอบสวนในฐานะ{ฐานะ} ดังนี้",
        WD_ALIGN_PARAGRAPH.THAI_JUSTIFY,
        indent=12,
        after=4,
    )
    para(doc, "{ย่อหน้าแจ้งสิทธิ}", WD_ALIGN_PARAGRAPH.THAI_JUSTIFY, indent=12, after=4)
    para(doc, "{เรื่องเล่า}", WD_ALIGN_PARAGRAPH.THAI_JUSTIFY, indent=12, after=4)
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    p = cell.paragraphs[0]
    thai_font(p.add_run("ถาม {qa.ถาม}"))
    p2 = cell.add_paragraph()
    thai_font(p2.add_run("ตอบ {qa.ตอบ}"))
    p3 = cell.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    thai_font(p3.add_run("[เสียงนาทีที่ {qa.เวลา}]"), 11)
    para(doc, "{บล็อกล่าม}", WD_ALIGN_PARAGRAPH.THAI_JUSTIFY, indent=12)
    para(doc, "ได้อ่านให้ฟังแล้ว รับว่าถูกต้อง จึงลงลายมือชื่อไว้เป็นหลักฐาน", indent=12, after=6)
    for role, name in (
        ("ผู้ให้การ", "{ผู้ให้การ}"),
        ("พนักงานสอบสวน", "{พงส}"),
        ("ทนายความ/ผู้ไว้วางใจ", "{ทนาย}"),
        ("ล่าม", "{ล่าม}"),
        ("ผู้บันทึก", "{ผู้บันทึก}"),
    ):
        para(doc, f"(ลงชื่อ)........................................{role}", WD_ALIGN_PARAGRAPH.RIGHT)
        para(doc, f"( {name} )", WD_ALIGN_PARAGRAPH.RIGHT, after=4)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def template_bytes() -> bytes:
    if default_storage.exists(CUSTOM_TEMPLATE):
        with default_storage.open(CUSTOM_TEMPLATE, "rb") as f:
            return f.read()
    if not DEFAULT_TEMPLATE.exists():
        LIBRARY.mkdir(exist_ok=True)
        DEFAULT_TEMPLATE.write_bytes(build_template())
    return DEFAULT_TEMPLATE.read_bytes()


def values(session: InterviewSession, draft, code: str) -> dict:
    cp = session.party
    p = cp.person
    officer = session.created_by
    people = {r: [x for x in session.participants.all() if x.role == r] for r in Participant.Role.values}
    interp = people["interpreter"][0] if people["interpreter"] else None
    counsel = (people["lawyer"] or people["trusted"] or [None])[0]
    started = timezone.localtime(session.recording.closed_at - timezone.timedelta(seconds=session.recording.duration))
    from documents.context import age

    narrative = session.statement_format == "narrative"
    rights = ""
    if session.is_suspect:
        rights = ("ก่อนถามคำให้การ พนักงานสอบสวนได้แจ้งข้อกล่าวหาและแจ้งให้ทราบว่า ผู้ต้องหามีสิทธิให้การหรือไม่ให้การก็ได้ "
                  "ถ้าให้การ ถ้อยคำที่ให้การนั้นอาจใช้เป็นพยานหลักฐานในการพิจารณาคดีได้ และมีสิทธิให้ทนายความหรือผู้ซึ่งตนไว้วางใจ"
                  "เข้าฟังการสอบปากคำได้ ตาม ป.วิ.อาญา มาตรา ๑๓๔/๔ (บันทึกอยู่ในไฟล์เสียง)")  # fmt: skip
    return {
        # unknown age/address (e.g. a walk-in complainant) print as dotted lines to be written in by hand
        "ฐานะ": cp.get_role_display(), "ผู้ให้การ": p.full_name, "อายุ": age(p.birth_date) or "......",
        "ที่อยู่": p.address or "..................................................",
        "เลขคดี": session.case.case_no or session.case.receipt_no or str(session.case), "ข้อหา": session.case.offense or "-",
        "สถานที่": session.place, "วันที่": thai.format_date(started.date(), "long"), "เวลาเริ่ม": f"{started:%H.%M} น.",
        "ย่อหน้าแจ้งสิทธิ": rights,
        "เรื่องเล่า": draft.narrative if narrative else "",
        "qa": [] if narrative else [{"ลำดับ": i, "ถาม": q.question, "ตอบ": q.answer, "เวลา": f"{thai.mmss(q.start)}–{thai.mmss(q.end)}"}
                                    for i, q in enumerate(draft.pairs.all(), start=1)],
        "บล็อกล่าม": f"ข้าฯ {interp.name} ล่ามภาษา{interp.language or '……'} ได้แปลคำถามและคำตอบให้ผู้ให้การและพนักงานสอบสวนฟังโดยถูกต้องแล้ว" if interp else "",
        "พงส": officer.display_name, "ตำแหน่ง": officer.position, "ทนาย": counsel.name if counsel else "", "ล่าม": interp.name if interp else "",
        "ผู้บันทึก": people["recorder"][0].name if people["recorder"] else officer.display_name,
        "รหัสเอกสาร": code, "sha256": session.recording.sha256,
    }  # fmt: skip


def transcript_docx(session) -> bytes:
    """คำพูดคำต่อคำ: continuous speech by speaker, then every segment with its time (to find it in the audio)."""
    from docx import Document

    from documents.management.commands.build_doc_templates import thai_font

    from .services import verbatim_turns

    doc = Document()
    doc.styles["Normal"].font.name = settings.DOC_FONT
    roles = dict(session.speaker_maps.values_list("label", "role"))
    names = dict(Participant.Role.choices)
    confirmed = session.status == InterviewSession.Status.CONFIRMED
    thai_font(doc.add_paragraph().add_run("ข้อความถอดเสียงคำต่อคำ"), 18, True)
    thai_font(
        doc.add_paragraph().add_run(
            f"{session} · เสียงยาว {thai.mmss(session.recording.duration)} · SHA-256 {session.recording.sha256}"
        ),
        12,
    )
    thai_font(
        doc.add_paragraph().add_run(
            "ตรวจทานและยืนยันแล้ว" if confirmed else "ร่างโดย AI – รอตรวจ: ตรวจกับเสียงก่อนนำไปใช้ในสำนวน"
        ),
        12,
        True,
    )
    thai_font(doc.add_paragraph().add_run("ข้อความต่อเนื่อง"), 14, True)
    for turn in verbatim_turns(session):
        p = doc.add_paragraph()
        thai_font(p.add_run(f"{turn['who']} ({thai.mmss(turn['start'])}): "), 14, True)
        thai_font(p.add_run(turn["text"]), 14)
    thai_font(doc.add_paragraph().add_run("ข้อความพร้อมเวลา"), 14, True)
    t = doc.add_table(rows=1, cols=3)
    t.style = "Table Grid"
    for cell, text in zip(t.rows[0].cells, ("เวลา", "ผู้พูด", "ข้อความ"), strict=True):
        thai_font(cell.paragraphs[0].add_run(text), 12, True)
    for s in session.segments.all():
        row = t.add_row().cells
        for cell, text in zip(
            row, (f"{thai.mmss(s.start)}–{thai.mmss(s.end)}", names.get(roles.get(s.speaker), s.speaker), s.text), strict=True
        ):
            thai_font(cell.paragraphs[0].add_run(text), 12)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def summary_docx(session, draft) -> bytes:
    """สรุปการสอบปากคำ: สรุปใจความสำคัญ · เรื่องราว · คำที่พบบ่อย — เอกสารประกอบสำนวน ไม่ใช่คำให้การ."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    from documents.management.commands.build_doc_templates import para, thai_font

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = Mm(25), Mm(20), Mm(30), Mm(20)
    doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = settings.DOC_FONT, Pt(16)
    confirmed = draft.locked and session.confirmed_at
    label = (f"ตรวจแล้วโดย {session.confirmed_by.display_name} เมื่อ {thai.format_datetime(timezone.localtime(session.confirmed_at))}"
             if confirmed else "ร่างโดย AI – รอตรวจ")  # fmt: skip
    para(doc, label, WD_ALIGN_PARAGRAPH.RIGHT, 12, True)
    para(doc, "สรุปการสอบปากคำ", WD_ALIGN_PARAGRAPH.CENTER, 20, True, after=4)
    para(doc, "เอกสารประกอบการทำสำนวน ไม่ใช่บันทึกคำให้การ", WD_ALIGN_PARAGRAPH.CENTER, 12, after=8)
    rec = session.recording
    started = timezone.localtime(rec.closed_at - timezone.timedelta(seconds=rec.duration)) if rec and rec.closed_at else None
    for k, v in (
        ("คดี", session.case.case_no or session.case.receipt_no or str(session.case)),
        ("ผู้ให้การ", f"{session.party.person.full_name} ({session.party.get_role_display()})"),
        ("วันที่สอบปากคำ", thai.format_datetime(started) if started else "-"),
        ("ความยาวเสียง", thai.mmss(rec.duration) if rec else "-"),
        ("SHA-256 ไฟล์เสียง", rec.sha256 if rec else "-"),
        ("ร่างฉบับที่", f"{draft.version} ({'AI ในเครื่อง' if draft.summary_source == 'ai' else 'ระบบดึงจากคำพูด'})"),
    ):
        p = para(doc, "", size=14)
        thai_font(p.add_run(f"{k}  "), 14, True)
        thai_font(p.add_run(str(v)), 14)
    para(doc, "๑. สรุปใจความสำคัญ", size=16, bold=True, after=2).paragraph_format.space_before = Pt(10)
    for point in draft.points or ["-"]:
        p = doc.add_paragraph(style="List Bullet")
        thai_font(p.add_run(point), 16)
    para(doc, "๒. เรื่องราวตามคำบอกเล่า", size=16, bold=True, after=2).paragraph_format.space_before = Pt(10)
    for chunk in (draft.narrative or "-").split("\n"):
        if chunk.strip():
            para(doc, chunk.strip(), WD_ALIGN_PARAGRAPH.THAI_JUSTIFY, indent=12, after=4)
    para(doc, "๓. คำที่พบบ่อย", size=16, bold=True, after=2).paragraph_format.space_before = Pt(10)
    words = draft.keywords or []
    t = doc.add_table(rows=1, cols=3)
    t.style = "Table Grid"
    for cell, text in zip(t.rows[0].cells, ("ลำดับ", "คำ", "จำนวนครั้ง"), strict=True):
        thai_font(cell.paragraphs[0].add_run(text), 14, True)
    for i, (word, count) in enumerate(words, start=1):
        row = t.add_row().cells
        for cell, text in zip(row, (str(i), word, str(count)), strict=True):
            thai_font(cell.paragraphs[0].add_run(text), 14)
    if draft.narrative_flags:
        para(doc, "๔. ข้อสังเกตจากระบบ (ต้องฟังเสียงตรวจ)", size=16, bold=True, after=2).paragraph_format.space_before = Pt(10)
        for flag in draft.narrative_flags:
            p = doc.add_paragraph(style="List Bullet")
            thai_font(p.add_run(flag), 14)
    para(doc, "ข้อความทั้งหมดดึงจากเสียงบันทึก ระบบตรวจตัวเลขและข้อความเทียบกับเสียงแล้ว พนักงานสอบสวนต้องฟังเสียงตรวจก่อนใช้",
         size=11).paragraph_format.space_before = Pt(10)  # fmt: skip
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def generate(session_id: int, user_id: int) -> InterviewDocument:
    from accounts.models import User

    from .services import dumps, sha256_file, transcript_json

    session = InterviewSession.objects.select_related("party__person", "case", "recording", "created_by").get(pk=session_id)
    user = User.objects.get(pk=user_id)
    draft = session.drafts.filter(locked=True).first()
    code = f"CCID4-INT-{session.pk}-{draft.version}-{session.recording.sha256[:8].upper()}"
    template = template_bytes()
    narrative = session.statement_format == "narrative"
    if narrative and "เรื่องเล่า" not in engine.find_placeholders(template):
        raise ValueError(
            "แบบฟอร์มคำให้การที่อัปโหลดไม่มีช่อง {เรื่องเล่า} จึงออกคำให้การแบบบรรยายไม่ได้ — เพิ่มช่องนี้ในแบบฟอร์ม"
        )
    data, missing = engine.fill(template, values(session, draft, code), thai_digits=False, allow_empty=OPTIONAL,
                                drop_empty_rows={"qa"} if narrative else ())  # fmt: skip
    if missing:
        raise ValueError("แบบฟอร์มมีช่องที่ระบบไม่มีค่า: " + ", ".join(missing))
    doc = InterviewDocument(session=session, draft=draft, code=code, created_by=user)
    name = f"คำให้การ-{code}"
    doc.docx.save(f"{name}.docx", ContentFile(data), save=False)
    pdf = docx_to_pdf(data) if soffice_available() else None
    if pdf:
        doc.pdf.save(f"{name}.pdf", ContentFile(pdf), save=False)
    files = {
        f"audio/{Path(session.recording.original.name).name}": None,
        "transcript.json": dumps(transcript_json(session)),
        "transcript.docx": transcript_docx(session),
        f"สรุปการสอบปากคำ-{code}.docx": summary_docx(session, draft),
        f"{name}.docx": data,
    }
    if pdf:
        files[f"{name}.pdf"] = pdf
        files["transcript.pdf"] = docx_to_pdf(files["transcript.docx"])
    import hashlib

    manifest = {"document_code": code, "created_at": timezone.now().isoformat(), "created_by": user.display_name,
                "audio_sha256_at_close": session.recording.sha256, "files": {}}  # fmt: skip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, content in files.items():
            if content is None:
                z.write(session.recording.original.path, arc)
                manifest["files"][arc] = sha256_file(session.recording.original.path)
            else:
                z.writestr(arc, content)
                manifest["files"][arc] = hashlib.sha256(content).hexdigest()
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    doc.evidence_zip.save(f"หลักฐาน-{code}.zip", ContentFile(buf.getvalue()), save=False)
    doc.hashes = manifest["files"]
    doc.save()
    log(user, "export", "interview", session.pk, f"ส่งออกคำให้การจากเสียง {code} และชุดหลักฐาน")
    return doc
