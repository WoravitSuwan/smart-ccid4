"""Phase 10: เทมเพลตจัดการเอง (B11) และรวมสำนวนเป็น PDF เล่มเดียว (B12). Fictional data."""

import io
import shutil

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from docx import Document as NewDoc
from PIL import Image

from audit.models import AuditLog
from cases.tests.factories import CaseFactory, PartyFactory, PersonFactory
from documents import bundle, catalog, services
from documents import templates_admin as T
from documents.models import CaseBundle, DocumentJob, IssuedDocument, TemplateVersion
from evidence import services as evidence

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
HAS_SOFFICE = shutil.which("soffice") is not None


def docx_with(*lines) -> bytes:
    d = NewDoc()
    for line in lines:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def text_of(data: bytes) -> str:
    return "".join(t.text or "" for t in NewDoc(io.BytesIO(data)).element.body.iter(W))


@pytest.fixture
def head(make_user):
    return make_user(role="supervisor", username="head", unit_group="กก.1")


@pytest.fixture
def case(investigator):
    investigator.rank, investigator.unit_group = "ร.ต.อ.", "กก.1"
    investigator.save()
    c = CaseFactory(owner=investigator, case_no="77/2569", dr_complaint_no="12", dr_complaint_at=timezone.now())
    PartyFactory(case=c, role="accuser", person=PersonFactory(first_name="ผู้แจ้ง", last_name="สมมติ", citizen_id="1100000000061"))
    return c


def issue(case, user, key, formats="docx"):
    job = DocumentJob.objects.create(case=case, request=[{"key": key}], formats=formats, created_by=user)
    services.run_job(job.pk)
    return IssuedDocument.objects.filter(case=case, key=key).first()


# ---------------------------------------------------------------- B11
@pytest.mark.django_db
def test_upload_scan_map_activate_and_rollback(case, head, investigator):
    up = SimpleUploadedFile("แบบของหน่วย.docx", docx_with("คดีที่ {คดี.เลขคดี}", "ผู้แจ้งชื่อ {ชื่อผู้แจ้ง}", "หมายเหตุ {ช่องเขียนมือ}"))
    tv = T.upload("withdraw_complaint", head, up, "แบบใหม่ของหน่วย")
    assert tv.version == 1 and set(tv.placeholders) == {"คดี.เลขคดี", "ชื่อผู้แจ้ง", "ช่องเขียนมือ"}
    assert tv.mapping["คดี.เลขคดี"] == "คดี.เลขคดี" and tv.mapping["ชื่อผู้แจ้ง"] == ""  # known names are matched by themselves
    T.save_mapping(tv, head, {"คดี.เลขคดี": "คดี.เลขคดี", "ชื่อผู้แจ้ง": "ผู้กล่าวหา.ชื่อเต็ม", "ช่องเขียนมือ": ""}, "arabic", True)
    doc = catalog.get("withdraw_complaint")
    assert doc.template_version() == tv.label and doc.number_style() == "arabic"
    issued = issue(case, investigator, "withdraw_complaint")
    with issued.docx.open("rb") as f:
        text = text_of(f.read())
    assert "คดีที่ 77/2569" in text and "นายผู้แจ้ง สมมติ" in text and "{" not in text and "……" not in text
    assert issued.template_version == tv.label  # the issued document remembers the version it used
    T.use_builtin("withdraw_complaint", head)
    assert catalog.get("withdraw_complaint").upload() is None and catalog.get("withdraw_complaint").template_version() != tv.label


@pytest.mark.django_db
def test_rejects_files_without_placeholders_or_not_word(head):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="ไม่พบช่อง"):
        T.upload("withdraw_complaint", head, SimpleUploadedFile("a.docx", docx_with("ไม่มีช่อง")))
    with pytest.raises(ValidationError, match=".docx"):
        T.upload("withdraw_complaint", head, SimpleUploadedFile("a.pdf", b"%PDF-1.4"))


@pytest.mark.django_db
def test_unit_can_add_a_new_document_type(case, head, investigator, client):
    client.force_login(head)
    r = client.post(
        reverse("doctemplates:add_custom"), {"title": "หนังสือขอข้อมูลแพลตฟอร์ม (แบบของหน่วย)", "group": "รับแจ้ง", "per": ""}
    )
    key = r["Location"].rstrip("/").split("/")[-1]
    _, st = services.states(case)
    state = next(s for s in st if s.doc.key == key)
    assert not state.ready and "ยังไม่ได้อัปโหลด" in state.missing[0].label
    up = SimpleUploadedFile("x.docx", docx_with("เรียน ผู้ให้บริการ", "คดี {คดี.เลขคดี} ฐานความผิด {คดี.ฐานความผิด}"))
    r = client.post(reverse("doctemplates:upload", args=[key]), {"file": up})
    tv = TemplateVersion.objects.get(key=key)
    assert r["Location"] == reverse("doctemplates:mapping", args=[tv.pk])
    html = client.get(r["Location"]).content.decode()
    assert "ชื่อตรงกับระบบ" in html
    client.post(
        r["Location"],
        {"map-0": tv.mapping[tv.placeholders[0]], "map-1": tv.mapping[tv.placeholders[1]], "digits": "thai", "activate": "on"},
    )
    _, st = services.states(case)
    assert next(s for s in st if s.doc.key == key).ready
    issued = issue(case, investigator, key)
    with issued.docx.open("rb") as f:
        assert "๗๗/๒๕๖๙" in text_of(f.read())
    client.force_login(investigator)
    assert client.get(reverse("doctemplates:index")).status_code == 403
    assert "หนังสือขอข้อมูลแพลตฟอร์ม" in client.get(reverse("documents:page", args=[case.pk])).content.decode()


@pytest.mark.django_db
def test_try_fill_with_a_case_is_logged(case, head, client):
    tv = T.upload("withdraw_complaint", head, SimpleUploadedFile("a.docx", docx_with("{คดี.เลขคดี}")))
    client.force_login(head)
    r = client.post(reverse("doctemplates:try", args=[tv.pk]), {"case": case.pk})
    assert r.status_code == 200 and "๗๗/๒๕๖๙" in text_of(r.content)  # this form prints Thai digits
    assert AuditLog.objects.filter(action="export", object_type="template").exists()


# ---------------------------------------------------------------- B12
@pytest.mark.django_db
def test_bundle_candidates_follow_the_case_file_order(case, investigator):
    assert issue(case, investigator, "withdraw_complaint")
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (200, 30, 30)).save(buf, "PNG")
    evidence.receive(case, investigator, SimpleUploadedFile("สลิป.png", buf.getvalue(), "image/png"))
    parts = bundle.candidates(case)
    assert parts[0].token.startswith("doc:") and parts[-1].token.startswith("ev:") and parts[-1].title == "พยานหลักฐาน: สลิป"


@pytest.mark.django_db
@pytest.mark.skipif(not HAS_SOFFICE, reason="LibreOffice not installed")
def test_bundle_pdf_has_toc_page_numbers_and_exporter_stamp(case, investigator, client_inv, django_capture_on_commit_callbacks):
    from pypdf import PdfReader

    issue(case, investigator, "withdraw_complaint", formats="pdf")
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (20, 120, 200)).save(buf, "PNG")
    ev = evidence.receive(case, investigator, SimpleUploadedFile("ภาพหน้าจอ.png", buf.getvalue(), "image/png"))
    parts = [p.token for p in bundle.candidates(case)]
    post = {"part": parts} | {f"order-{t}": str(len(parts) - i) for i, t in enumerate(parts)}  # reversed: evidence first
    r = client_inv.post(reverse("documents:case_bundle", args=[case.pk]), post)
    b = CaseBundle.objects.get()
    assert r["Location"].endswith(f"?b={b.pk}") and b.status == "done", b.error
    assert b.request[0] == f"ev:{ev.pk}" and b.items[0]["title"] == "พยานหลักฐาน: ภาพหน้าจอ"
    with b.file.open("rb") as f:
        reader = PdfReader(io.BytesIO(f.read()))
    assert len(reader.pages) == b.pages == 1 + sum(i["pages"] for i in b.items)
    first, last = reader.pages[0].extract_text(), reader.pages[-1].extract_text()
    # pypdf's Thai text extraction is lossy, so check the Latin/digit parts of the TOC, stamp and page number
    assert "77/2569" in first and "......" in first  # table of contents with dotted page numbers
    assert "CCID4" in last and "77/2569" in last  # watermark: who/when/case on every page
    assert f"{b.pages} / {b.pages}" in last  # "หน้า n / N"
    assert [o.title for o in reader.outline][0] == "พยานหลักฐาน: ภาพหน้าจอ"
    assert AuditLog.objects.filter(action="export", object_type="case_bundle").exists()
    assert case.tasks.filter(title__contains="รวมสำนวน").exists()
    r = client_inv.get(reverse("documents:case_bundle_download", args=[case.pk, b.pk]))
    assert r.status_code == 200 and AuditLog.objects.filter(action="download", object_type="case_bundle").exists()
