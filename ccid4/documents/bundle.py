"""รวมสำนวนเป็น PDF เล่มเดียว (B12): เอกสารที่ออกแล้ว + คำให้การจากเสียง + พยานหลักฐาน PDF/ภาพ เรียงตามบัญชีสำนวน
→ สารบัญ · เลขหน้า "หน้า n/N" · ลายน้ำชื่อผู้ส่งออกและเวลา · bookmark ต่อรายการ · audit."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from audit.services import log
from core import thai
from core.convert import docx_to_pdf, soffice_available

from . import catalog
from .models import CaseBundle

A4 = (595.276, 841.89)  # points


@dataclass
class Part:
    token: str  # doc:<id> | int:<id> | ev:<id>
    title: str
    kind: str
    order: float
    note: str = ""


def candidates(case) -> list[Part]:
    """Everything that can go into the bundle, in case-file order (document number), latest version of each."""
    docs = catalog.load()[1]
    parts, seen = [], set()
    for d in case.issued_documents.order_by("-version", "-created_at"):
        k = (d.key, d.party_id, d.letter_id)
        if k in seen:
            continue
        seen.add(k)
        no = docs[d.key].no if d.key in docs else 99
        title = f"{d.title}{' (' + d.item_label + ')' if d.item_label else ''}"
        parts.append(
            Part(
                f"doc:{d.pk}",
                title,
                "เอกสารที่ออก",
                no + (0.001 * (d.party_id or 0)),
                f"ฉบับที่ {d.version} · {'PDF' if d.pdf else 'Word'}",
            )
        )
    from interviews.models import InterviewDocument

    latest = {}
    for idoc in (
        InterviewDocument.objects.filter(session__case=case).select_related("session__party__person").order_by("-created_at")
    ):
        latest.setdefault(idoc.session_id, idoc)
    for idoc in latest.values():
        cp = idoc.session.party
        parts.append(
            Part(
                f"int:{idoc.pk}",
                f"บันทึกคำให้การ{cp.get_role_display()} {cp.person.full_name} (จากเสียง)",
                "คำให้การจากเสียง",
                5.5,
                idoc.code,
            )
        )
    for ev in case.evidence_files.filter(withdrawn_at__isnull=True, kind__in=("pdf", "image")):
        parts.append(Part(f"ev:{ev.pk}", f"พยานหลักฐาน: {ev.title}", "พยานหลักฐาน", 900 + ev.pk, f"SHA-256 {ev.sha256[:12]}…"))
    return sorted(parts, key=lambda p: p.order)


# ---------------------------------------------------------------- building
def image_pdf(path: str) -> bytes:
    from PIL import Image

    page_w, page_h = 1240, 1754  # A4 at 150 dpi
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((page_w - 160, page_h - 240))
        page = Image.new("RGB", (page_w, page_h), "white")
        page.paste(im, ((page_w - im.width) // 2, 120))
        out = io.BytesIO()
        page.save(out, "PDF", resolution=150)
        return out.getvalue()


def part_pdf(case, token: str) -> tuple[bytes, str]:
    kind, _, pk = token.partition(":")
    if kind == "doc":
        d = case.issued_documents.get(pk=pk)
        title = f"{d.title}{' (' + d.item_label + ')' if d.item_label else ''}"
        if d.pdf:
            with d.pdf.open("rb") as f:
                return f.read(), title
        with d.docx.open("rb") as f:
            return docx_to_pdf(f.read()), title
    if kind == "int":
        from interviews.models import InterviewDocument

        idoc = InterviewDocument.objects.select_related("session__party__person").get(pk=pk, session__case=case)
        cp = idoc.session.party
        title = f"บันทึกคำให้การ{cp.get_role_display()} {cp.person.full_name} (จากเสียง)"
        f = idoc.pdf or idoc.docx
        with f.open("rb") as fh:
            data = fh.read()
        return (data if idoc.pdf else docx_to_pdf(data)), title
    if kind == "ev":
        ev = case.evidence_files.get(pk=pk, withdrawn_at__isnull=True)
        if ev.kind == "pdf":
            with ev.file.open("rb") as f:
                return f.read(), f"พยานหลักฐาน: {ev.title}"
        if ev.kind == "image":
            return image_pdf(ev.file.path), f"พยานหลักฐาน: {ev.title}"
    raise ValueError(f"รวมรายการ {token} ไม่ได้")


def _doc():
    from docx import Document
    from docx.shared import Mm, Pt

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = Mm(25), Mm(20), Mm(30), Mm(20)
    sec.header_distance = sec.footer_distance = Mm(8)
    doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = settings.DOC_FONT, Pt(16)
    return doc


def toc_pdf(case, entries: list[tuple[str, int, int]], offset: int) -> bytes:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm

    from documents.management.commands.build_doc_templates import para, thai_font

    doc = _doc()
    para(doc, "สารบัญสำนวนการสอบสวน", WD_ALIGN_PARAGRAPH.CENTER, 20, True, after=4)
    para(doc, f"คดี {case.label}{' · ' + case.offense if case.offense else ''}", WD_ALIGN_PARAGRAPH.CENTER, 16, after=10)
    for i, (title, start, _pages) in enumerate(entries, start=1):
        p = doc.add_paragraph()
        p.paragraph_format.tab_stops.add_tab_stop(Mm(160), alignment=2, leader=1)  # right-aligned page number with dots
        p.paragraph_format.space_after = 0
        thai_font(p.add_run(f"{thai.to_thai_digits(str(i))}. {title}\t{thai.to_thai_digits(str(start + offset))}"))
    para(
        doc, f"รวม {thai.to_thai_digits(str(sum(e[2] for e in entries)))} หน้า (ไม่รวมสารบัญ)", WD_ALIGN_PARAGRAPH.RIGHT, 14
    ).paragraph_format.space_before = Mm(4)
    out = io.BytesIO()
    doc.save(out)
    return docx_to_pdf(out.getvalue())


def overlay_pdf(total: int, stamp: str) -> bytes:
    """N empty A4 pages: header = watermark (who exported, when), footer = "หน้า n/N"."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import RGBColor

    from documents.management.commands.build_doc_templates import thai_font

    doc = _doc()
    sec = doc.sections[0]
    head = sec.header.paragraphs[0]
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = thai_font(head.add_run(stamp), 10)
    r.font.color.rgb = RGBColor(0x8A, 0x94, 0xA6)  # print colour for the PDF stamp (not a screen design token)
    foot = sec.footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def field(paragraph, instr):
        f = OxmlElement("w:fldSimple")
        f.set(qn("w:instr"), instr)
        run = OxmlElement("w:r")
        t = OxmlElement("w:t")
        t.text = "1"
        run.append(t)
        f.append(run)
        paragraph._p.append(f)

    thai_font(foot.add_run("หน้า "), 12)
    field(foot, "PAGE")
    thai_font(foot.add_run(" / "), 12)
    field(foot, "NUMPAGES")
    # one paragraph per page: LibreOffice merges several page breaks inside a single paragraph into one
    doc.add_paragraph(" ")
    for _ in range(total - 1):
        p = doc.add_paragraph(" ")
        p.paragraph_format.page_break_before = True
    out = io.BytesIO()
    doc.save(out)
    return docx_to_pdf(out.getvalue(), timeout=300)


def build(bundle_id: int) -> CaseBundle:
    from pypdf import PdfReader, PdfWriter, Transformation

    from deadlines.services import log_action

    b = CaseBundle.objects.select_related("case", "created_by").get(pk=bundle_id)
    case, user = b.case, b.created_by
    try:
        if not soffice_available():
            raise RuntimeError("เครื่องนี้ไม่มี LibreOffice จึงรวมเป็น PDF ไม่ได้")
        readers, entries = [], []
        page = 1
        for token in b.request:
            data, title = part_pdf(case, token)
            r = PdfReader(io.BytesIO(data))
            n = len(r.pages)
            if n:
                readers.append((r, title))
                entries.append((title, page, n))
                page += n
        if not readers:
            raise RuntimeError("ยังไม่ได้เลือกรายการ")
        toc_pages, toc = 1, None
        for _ in range(3):  # the TOC length shifts every page number after it
            toc = PdfReader(io.BytesIO(toc_pdf(case, entries, toc_pages)))
            if len(toc.pages) == toc_pages:
                break
            toc_pages = len(toc.pages)
        total = len(toc.pages) + page - 1
        now = timezone.localtime()
        stamp = f"ส่งออกโดย {user.display_name} · {thai.format_datetime(now)} · CCID4 · คดี {case.label}"
        over = PdfReader(io.BytesIO(overlay_pdf(total, stamp)))
        w = PdfWriter()
        for p in toc.pages:
            w.add_page(p)
        starts = []
        for r, title in readers:
            starts.append((title, len(w.pages)))
            for p in r.pages:
                w.add_page(p)
        for i, p in enumerate(w.pages):
            o = over.pages[min(i, len(over.pages) - 1)]
            pw, ph = float(p.mediabox.width), float(p.mediabox.height)
            ow, oh = float(o.mediabox.width), float(o.mediabox.height)
            p.merge_transformed_page(o, Transformation().scale(pw / ow, ph / oh))
        for title, idx in starts:
            w.add_outline_item(title[:120], idx)
        w.add_metadata({"/Title": f"สำนวนคดี {case.label}", "/Author": user.display_name, "/Producer": "CCID4"})
        out = io.BytesIO()
        w.write(out)
        data = out.getvalue()
        b.items = [{"title": t, "start": s + len(toc.pages), "pages": n} for t, s, n in entries]
        b.pages, b.sha256, b.status, b.error = total, hashlib.sha256(data).hexdigest(), CaseBundle.Status.DONE, ""
        b.file.save(f"สำนวนรวม-{case.pk}-{now:%Y%m%d-%H%M%S}.pdf", ContentFile(data), save=False)
        b.save()
        log(user, "export", "case_bundle", b.pk, f"รวมสำนวนเป็น PDF {total} หน้า {len(entries)} รายการ คดี {case}")
        log_action(case, user, f"รวมสำนวนเป็น PDF เล่มเดียว {total} หน้า", reason=f"SHA-256 {b.sha256[:16]}…")
    except Exception as e:  # noqa: BLE001 - shown to the officer
        b.status, b.error = CaseBundle.Status.FAILED, f"รวมสำนวนไม่สำเร็จ: {e}"
        b.save(update_fields=["status", "error"])
    return b
