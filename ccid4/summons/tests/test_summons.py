"""Phase 7 (B1): acceptance checks from docs/SPEC_summons_system.md plus permissions and integration with CCID4 cases."""

import io
import re
import zipfile
from datetime import date
from pathlib import Path

import openpyxl
import pytest
from django.core.files import File
from django.urls import reverse
from django.utils import timezone
from docx import Document

from audit.models import AuditLog
from cases.models import Case
from core.convert import soffice_available
from summons import importer, postcodes, services
from summons import thai_format as tf
from summons.models import Complainant, ImportBatch, RegisterCase, RegisterInvestigator, RegisterStatus, RegisterUnit

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data"
W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"


def do_import(user, name, sheets=None):
    b = ImportBatch(created_by=user, file_name=name)
    with open(SAMPLE / name, "rb") as fh:
        b.file.save(name, File(fh), save=False)
    book = importer.open_book(b.file.path)
    b.kind = importer.detect_kind(book)
    b.sheets = sheets or [ws.title for ws in book.worksheets]
    b.mapping = importer.auto_mapping(b.kind, importer.headers_of(book.worksheets[0]))
    b.save()
    return b, importer.apply(b, user)


@pytest.fixture
def clerk(make_user):
    return make_user(role="clerk", username="clerk")


@pytest.fixture
def loaded(clerk):
    do_import(clerk, "mock_complainants.xlsx")
    do_import(clerk, "mock_case_register.xlsx")
    for inv in RegisterInvestigator.objects.all():
        inv.rank_full, inv.sign_name, inv.position, inv.phone = "พันตำรวจโท", inv.name, "สารวัตร (สอบสวน)", "053123456"
        inv.save()
    RegisterUnit.objects.update(issue_place="กองบังคับการตำรวจสืบสวนสอบสวนอาชญากรรมทางเทคโนโลยี 4", appointment_place="กองกำกับการ 1 (ที่อยู่สมมติ)",
                                sender_address="ที่อยู่หน่วยสมมติ 50180")  # fmt: skip
    return clerk


def doc_text(path_or_bytes) -> str:
    doc = Document(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes)
    parts = []
    for part in [doc.element.body] + [s.header._element for s in doc.sections] + [s.footer._element for s in doc.sections]:
        parts += [t.text or "" for t in part.iter(W_T)]
    return "".join(parts)


# ---------------------------------------------------------------- acceptance (spec)
@pytest.mark.django_db
def test_import_counts_match_the_spec(loaded):
    assert Complainant.objects.count() == 50
    assert RegisterCase.objects.count() == 40
    by_sheet = {s: RegisterCase.objects.filter(report_date__year=y).count() for s, y in (("2568", 2025), ("2569", 2026))}
    assert by_sheet == {"2568": 15, "2569": 25}
    assert RegisterCase.objects.filter(complainant__isnull=False).count() == 40  # all matched
    open_ = RegisterCase.objects.exclude(status__is_final=True)
    assert open_.count() == 32
    assert open_.filter(complainant__house_no="").count() == 5
    assert open_.filter(complainant__province="").count() == 2


@pytest.mark.django_db
def test_reimport_adds_no_rows_and_reports_changes(loaded, clerk):
    rc = RegisterCase.objects.exclude(status__name="สิ้นสุด").first()
    rc.status = RegisterStatus.objects.get(name="สิ้นสุด")
    rc.save()
    _, counts = do_import(clerk, "mock_case_register.xlsx")
    _, counts2 = do_import(clerk, "mock_complainants.xlsx")
    assert RegisterCase.objects.count() == 40 and Complainant.objects.count() == 50
    assert counts["new"] == 0 and counts["updated"] == 40 and counts["changes"] == {"สถานะเปลี่ยน": 1}
    assert counts2["new"] == 0
    assert Case.objects.count() == 40  # one CCID4 case per register row, not duplicated


@pytest.mark.django_db
def test_one_summons_has_no_braces_and_thai_be_dates(loaded, clerk):
    rc = RegisterCase.objects.exclude(status__is_final=True).filter(complainant__house_no__gt="").first()
    batch = services.start_batch(clerk, [rc.pk], {"formats": "both", "issued_on": date(2026, 2, 9), "envelopes": True})
    assert batch.status == "done", batch.error
    s = batch.summons.get()
    text = doc_text(s.docx.path)
    assert "{" not in text and "+14" not in text
    assert "๙" in text and "กุมภาพันธ์" in text and "๖๙" in text  # 9 Feb 2569 → ๙ กุมภาพันธ์ (พ.ศ. ๒๕)๖๙
    assert not re.search(r"[0-9]", text.replace(rc.receipt_no, ""))  # every digit in the form is Thai
    assert "{" not in doc_text(s.envelope.path)
    if soffice_available():
        from ai.ocr import pdf_text

        with s.pdf.open("rb") as f:
            pdf = pdf_text(f.read())
        assert "{" not in pdf and "กุมภาพันธ์" in pdf


@pytest.mark.django_db
def test_all_open_cases_in_one_pdf(loaded, clerk):
    """Spec: 32 summons in one PDF. One person has no address at all, which ป.วิ.อ. ม.53 forbids, so 31 are issued."""
    ids = list(RegisterCase.objects.exclude(status__is_final=True).values_list("pk", flat=True))
    batch = services.start_batch(clerk, ids, {"formats": "pdf", "combine": True, "envelopes": True})
    if batch.status == "pending":  # > 20: queued (eager in tests runs on commit)
        services.run_batch(batch.pk)
        batch.refresh_from_db()
    assert batch.done_count == 31 and batch.status == "done"
    assert "ขาดที่อยู่ผู้ถูกเรียก" in batch.error
    combined = doc_text(batch.combined_docx.path)
    assert "{" not in combined and combined.count("ใบรับหมายตำรวจ") == 31
    if soffice_available():
        from pypdf import PdfReader

        pages = len(PdfReader(batch.combined_pdf.path).pages)
        assert pages == 31 * 2  # each summons: form + receipt slip


@pytest.mark.django_db
def test_separate_files_in_zip_with_thai_names(loaded, clerk):
    ids = list(
        RegisterCase.objects.exclude(status__is_final=True).filter(complainant__house_no__gt="").values_list("pk", flat=True)[:3]
    )
    batch = services.start_batch(clerk, ids, {"formats": "docx", "envelopes": True})
    with zipfile.ZipFile(batch.bundle.path) as z:
        names = z.namelist()
    assert len([n for n in names if n.endswith("หมายเรียกครั้งที่1.docx")]) == 3
    assert all(re.match(r"^[A-Z]\d+_", n) for n in names)


# ---------------------------------------------------------------- import rules
def test_receipt_numbers_and_text_cells():
    assert importer.norm_receipt(" l0002 1063770 ") == "L00021063770"
    assert importer.cell_text(812345678, "phone") == "0812345678"
    assert importer.cell_text(123456789012, "citizen_id") == "0123456789012"
    assert importer.cell_text(12800.0) == "12800"


def test_postcodes_from_open_data():
    assert postcodes.lookup("ช้างเผือก", "เมืองเชียงใหม่", "เชียงใหม่") == "50300"
    assert postcodes.lookup("ต.ช้างเผือก", "อ.เมือง", "จ.เชียงใหม่") == "50300"
    assert postcodes.lookup("แขวงพระบรมมหาราชวัง", "เขตพระนคร", "กทม.") == "10200"
    assert postcodes.lookup("ไม่มีจริง", "ไม่มีจริง", "ไม่มีจริง") == ""


@pytest.mark.django_db
def test_postcode_is_filled_but_kept_separate(loaded):
    c = Complainant.objects.get(receipt_no="L00021063770")
    assert c.postcode == "" and c.postcode_filled == "50300" and c.address.endswith("50300")


@pytest.mark.django_db
def test_problem_rows_are_flagged_and_downloadable(loaded, clerk, client):
    b = ImportBatch.objects.filter(kind="complainants").first()
    assert b.issues.filter(problem="ไม่มีบ้านเลขที่").exists()
    client.force_login(clerk)
    r = client.get(reverse("summons:import_issues", args=[b.pk]))
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.active.max_row == b.issues.count() + 1


@pytest.mark.django_db
def test_new_status_found_on_import_is_not_final(clerk):
    do_import(clerk, "mock_case_register.xlsx")
    assert RegisterStatus.objects.get(name="สิ้นสุด").is_final
    assert not RegisterStatus.objects.filter(auto_created=True).exists()  # all sample statuses are known


@pytest.mark.django_db
def test_renamed_header_is_mapped_by_hand_and_remembered(clerk, client, tmp_path):
    wb = openpyxl.load_workbook(SAMPLE / "mock_complainants.xlsx")
    wb["Grid"]["B1"] = "คำนำหน้า"  # renamed header
    path = tmp_path / "renamed.xlsx"
    wb.save(path)
    client.force_login(clerk)
    with open(path, "rb") as fh:
        r = client.post(reverse("summons:import"), {"file": fh})
    batch = ImportBatch.objects.get()
    assert "คำนำหน้า" not in batch.mapping
    data = (
        {"sheets": ["Grid"], "action": "preview"} | {f"map:{h}": f for h, f in batch.mapping.items()} | {"map:คำนำหน้า": "title"}
    )
    client.post(r["Location"], data)
    assert importer.auto_mapping("complainants", ["คำนำหน้า"]) == {"คำนำหน้า": "title"}


# ---------------------------------------------------------------- dates and templates
def test_thai_format():
    assert tf.thai_date(date(2026, 2, 9)) == "๙ กุมภาพันธ์ ๒๕๖๙"
    assert tf.be_year_2(date(2026, 2, 9)) == "๖๙"


def test_default_appointment_skips_weekends_and_staggers():
    issued = date(2026, 11, 7)  # Saturday; +14 = Saturday 21 Nov → Monday 23 Nov
    a = timezone.localtime(services.default_appointment(issued))
    assert (a.date(), a.hour, a.minute) == (date(2026, 11, 23), 9, 0)
    b = timezone.localtime(services.default_appointment(issued, 2, stagger=True))
    assert (b.hour, b.minute) == (10, 0)


@pytest.mark.django_db
def test_v2_template_has_appointment_placeholders_and_original_is_untouched():
    before = (SAMPLE / "summons_template_sanitized.docx").read_bytes()
    services.ensure_templates()
    v2 = services.active_template("summons")
    assert v2.version == 2 and {"วันนัด", "เดือนนัด", "ปีนัด", "เวลานัด", "สถานที่นัด", "เหตุที่เรียก", "สถานที่ออกหมาย"} <= set(v2.placeholders)
    assert (SAMPLE / "summons_template_sanitized.docx").read_bytes() == before


@pytest.mark.django_db
def test_missing_signer_blocks_issue(loaded, clerk):
    rc = RegisterCase.objects.exclude(status__is_final=True).filter(complainant__house_no__gt="").first()
    rc.investigator.position = ""
    rc.investigator.save()
    item = services.plan([rc], {})[0]
    assert any("ตำแหน่งผู้ออกหมาย" in p for p in item.problems)


# ---------------------------------------------------------------- screens and permissions
@pytest.mark.django_db
def test_issue_flow_through_the_screens(loaded, clerk, client):
    client.force_login(clerk)
    ids = list(
        RegisterCase.objects.exclude(status__is_final=True).filter(complainant__house_no__gt="").values_list("pk", flat=True)[:2]
    )
    r = client.post(reverse("summons:issue"), {"ids": ids})
    assert r["Location"] == reverse("summons:issue")
    page = client.get(r["Location"])
    assert page.status_code == 200 and "ยืนยันออกหมาย" in page.content.decode()
    data = {"issued_on": "2026-11-16", "reason": services.DEFAULT_REASON, "thai_digits": "on", "formats": "docx", "envelopes": "on",
            "set_status": "on", "action": "confirm"}  # fmt: skip
    r = client.post(reverse("summons:issue"), data)
    batch_url = r["Location"]
    assert "/summons/batches/" in batch_url
    assert "ZIP แยกรายคน" in client.get(batch_url).content.decode()
    rc = RegisterCase.objects.get(pk=ids[0])
    assert rc.status.name == "ออกหมายเรียก" and rc.summons.count() == 1
    assert AuditLog.objects.filter(action="export", object_type="summons").count() == 2
    # the CCID4 case shows the appointment in its panel and the log
    assert rc.case.tasks.filter(source="auto", title__startswith="ออกหมายเรียกผู้กล่าวหาครั้งที่ 1").exists()
    from deadlines.services import case_deadlines

    assert any(i.deadline.key == "summons" for i in case_deadlines(rc.case))
    preview = client.post(reverse("summons:issue"), {"ids": [ids[0]]})
    assert preview.status_code == 302
    assert client.get(reverse("summons:detail", args=[ids[0]])).status_code == 200


@pytest.mark.django_db
def test_preview_before_confirming(loaded, clerk, client):
    client.force_login(clerk)
    rc = RegisterCase.objects.exclude(status__is_final=True).filter(complainant__house_no__gt="").first()
    client.post(reverse("summons:issue"), {"ids": [rc.pk]})
    r = client.get(
        reverse("summons:issue_preview"),
        {"issued_on": "2026-11-16", "reason": services.DEFAULT_REASON, "thai_digits": "on", "formats": "pdf"},
    )
    assert r.status_code == 200 and r["Content-Type"] in (
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@pytest.mark.django_db
def test_investigator_sees_only_own_cases(loaded, client, make_user):
    inv = RegisterInvestigator.objects.first()
    user = make_user(role="investigator", first_name=inv.name.split()[0], last_name=inv.name.split()[1])
    inv.user = user
    inv.save()
    client.force_login(user)
    html = client.get(reverse("summons:register")).content.decode()
    mine = RegisterCase.objects.filter(investigator=inv)
    other = RegisterCase.objects.exclude(investigator=inv).first()
    assert all(rc.receipt_no in html for rc in mine[:5]) and other.receipt_no not in html
    assert client.get(reverse("summons:detail", args=[other.pk])).status_code == 404
    assert client.get(reverse("summons:import")).status_code == 403


@pytest.mark.django_db
def test_imported_cases_go_to_matched_investigator(clerk, make_user):
    user = make_user(role="investigator", first_name="สมมติ", last_name="ใจดี")
    do_import(clerk, "mock_complainants.xlsx")
    do_import(clerk, "mock_case_register.xlsx")
    rc = RegisterCase.objects.filter(investigator__name="สมมติ ใจดี").first()
    assert rc.case.owner == user and rc.case.status == "imported"
    assert rc.case.parties.get(role="accuser").person.first_name == rc.complainant.first_name


@pytest.mark.django_db
def test_reveal_citizen_id_is_audited(loaded, clerk, client, make_user):
    rc = RegisterCase.objects.exclude(complainant__citizen_id="").first()
    sup = make_user(role="supervisor")
    client.force_login(sup)
    html = client.get(reverse("summons:detail", args=[rc.pk])).content.decode()
    assert rc.complainant.citizen_id not in html
    r = client.post(reverse("summons:reveal_id", args=[rc.pk]))
    assert r.content.decode() == rc.complainant.citizen_id
    assert AuditLog.objects.filter(action="reveal", object_type="complainant").exists()
    client.force_login(clerk)
    assert client.post(reverse("summons:reveal_id", args=[rc.pk])).status_code == 403


@pytest.mark.django_db
def test_register_export_keeps_original_columns(loaded, clerk, client):
    client.force_login(clerk)
    r = client.get(reverse("summons:register_xlsx"), {"open": "1"})
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    assert [c.value for c in ws[1]] == list(importer.REGISTER_COLUMNS.keys())
    assert ws.max_row == 33


@pytest.mark.django_db
def test_dashboard_numbers(loaded, clerk, client):
    client.force_login(clerk)
    ctx = client.get(reverse("summons:index")).context
    assert ctx["counts"]["total"] == 40 and ctx["counts"]["open"] == 32 and ctx["counts"]["never"] == 32
