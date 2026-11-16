"""Phase 4: document list, readiness, issuing (docx/PDF/ZIP), history and logs."""

import zipfile
from io import BytesIO

import pytest
from django.urls import reverse
from django.utils import timezone
from docx import Document

from accounts.models import Commander, CourtOrProsecutor, Unit
from audit.models import AuditLog
from cases.tests.factories import CaseFactory, PartyFactory, PersonFactory
from core.convert import soffice_available
from deadlines.models import Task
from documents import catalog, context, engine, services
from documents.models import IssuedDocument
from parties.models import PropertyItem, Statement


@pytest.fixture
def full_profile(investigator):
    investigator.rank, investigator.position, investigator.phone = "พ.ต.ต.", "สารวัตร (สอบสวน)", "0200000000"
    investigator.first_name, investigator.last_name = "สมมติ", "ทดสอบ"
    investigator.save()
    Unit.objects.create(
        user=investigator, name="กก.1 บก.สอท.4", address="ถนนสมมติ เขตทดสอบ", doc_prefix="ตช 0026.xx", phone="021111111"
    )
    Commander.objects.create(user=investigator, rank="พ.ต.อ.", name="ผู้กำกับ สมมติ", position="ผกก.1", is_default=True)
    CourtOrProsecutor.objects.create(user=investigator, kind="court", name="ศาลอาญา")
    CourtOrProsecutor.objects.create(user=investigator, kind="prosecutor", name="สำนักงานอัยการสูงสุด (สมมติ)")
    return investigator


@pytest.fixture
def case(investigator):
    c = CaseFactory(
        owner=investigator, case_type="online", case_no="99/2569", offense="ฉ้อโกงประชาชน", law_sections="ป.อ. ม.343",
        penalty_text="จำคุกไม่เกิน 5 ปี", max_penalty_months=60, court_type="criminal", circumstances="ผู้ต้องหาหลอกขายสินค้าออนไลน์",
        dr_complaint_item="5", dr_complaint_no="12", dr_complaint_at=timezone.now(), location="กรุงเทพมหานคร",
        incident_from=timezone.now(),
    )  # fmt: skip
    acc = PartyFactory(case=c, role="accuser", person=PersonFactory(first_name="ผู้แจ้ง", house_no="1", province="นนทบุรี"))
    Statement.objects.create(party=acc, version=1, text="ข้าพเจ้าถูกหลอกโอนเงิน 12000 บาท", created_by=investigator)
    PropertyItem.objects.create(case=c, name="เงินสด", value=12000)
    return c


def suspects(case, n=2):
    out = []
    for i in range(n):
        p = PartyFactory(
            case=case, role="suspect", order=i + 1,
            person=PersonFactory(first_name=f"ผู้ต้องหา{i + 1}", house_no=f"{i + 10}", province="ปทุมธานี", marks="แผลเป็นที่แขน"),
            arrested_at=timezone.now(), arrived_at=timezone.now(), custody_place="ห้องควบคุม",
        )  # fmt: skip
        Statement.objects.create(party=p, version=1, text="ให้การปฏิเสธ", created_by=case.owner)
        out.append(p)
    return out


def state(case, key):
    return next(s for s in services.states(case)[1] if s.doc.key == key)


# ---------------------------------------------------------------- catalog
def test_manifest_lists_26_documents_with_template_files():
    groups, docs = catalog.load()
    assert len(docs) == 26 and [d.no for d in docs.values()] == list(range(1, 27))
    for d in docs.values():
        assert d.group in groups and d.path.exists(), d.key
        assert d.per in (None, "accuser", "suspect", "witness", "people", "letter")


@pytest.mark.django_db
def test_every_placeholder_is_a_known_field(case, full_profile, investigator):
    """Catches typos in templates: every {placeholder} must resolve against a fully filled case."""
    sus = suspects(case, 1)[0]
    from cases.models import OutgoingLetter

    letter = OutgoingLetter.objects.create(
        case=case, template="bank", subject="ขอข้อมูล", recipient="ธนาคาร", fields={"bank": "สมมติ"}, created_by=investigator
    )
    for doc in catalog.load()[1].values():
        values = context.build(
            case, party=sus if doc.per not in (None, "letter") else None, letter=letter if doc.per == "letter" else None
        )
        for name in engine.find_placeholders(doc.template_bytes()):
            head = name.split(".")[0]
            assert engine.lookup(values, name) is not None or isinstance(values.get(head), list), f"{doc.key}: {{{name}}}"


def test_templates_use_the_official_font():
    from django.conf import settings

    doc = Document(catalog.get("daily_record").path)
    fonts = {r.font.name for p in doc.paragraphs for r in p.runs}
    assert fonts == {settings.DOC_FONT}


# ---------------------------------------------------------------- readiness (acceptance 2)
@pytest.mark.django_db
def test_missing_profile_locks_documents_and_says_what(case):
    s = state(case, "daily_record")
    assert not s.ready
    labels = {m.label: m.url for m in s.all_missing}
    assert "ชื่อหน่วย" in labels and labels["ชื่อหน่วย"].endswith("/accounts/profile/#unit")


@pytest.mark.django_db
def test_complete_profile_unlocks(case, full_profile):
    assert state(case, "daily_record").ready
    assert state(case, "progress_notice").ready
    assert not state(case, "exhibits").ready  # evidence register comes in phase 10


@pytest.mark.django_db
def test_missing_case_field_links_to_that_field(full_profile, investigator):
    case = CaseFactory(owner=investigator, dr_complaint_no="")
    m = {x.label: x.url for x in state(case, "daily_record").all_missing}
    url = m["เลขที่บันทึกประจำวันรับคำร้องทุกข์"]
    assert url.endswith("?focus=c-dr_complaint_no#info") and f"/cases/{case.pk}/" in url


@pytest.mark.django_db
def test_per_person_readiness(case, full_profile):
    a, b = suspects(case)
    b.person.house_no = b.person.province = b.person.tambon = b.person.amphoe = ""
    b.person.save()
    s = state(case, "summons_suspect")
    ready = {i.id: i.ready for i in s.items}
    assert s.ready and ready == {a.pk: True, b.pk: False}
    assert not state(case, "statement_witness").ready  # no witness yet
    assert "ยังไม่มีพยาน" in state(case, "statement_witness").all_missing[0].label


# ---------------------------------------------------------------- issuing (acceptance 5)
@pytest.mark.django_db
def test_issue_three_documents_for_two_suspects_as_zip(client_inv, case, full_profile):
    a, b = suspects(case)
    fmt = "both" if soffice_available() else "docx"
    data = {"doc": ["daily_record", "charge_notice", "statement_suspect"], "items-charge_notice": [a.pk, b.pk],
            "items-statement_suspect": [a.pk, b.pk], "format": fmt}  # fmt: skip
    r = client_inv.post(reverse("documents:issue", args=[case.pk]), data)
    assert r.status_code == 302 and "job=" in r["Location"]
    job = case.document_jobs.get()
    assert job.status == "done", job.error
    assert job.documents.count() == 5
    with zipfile.ZipFile(job.bundle.open("rb")) as z:
        names = z.namelist()
        assert sum(n.endswith(".docx") for n in names) == 5
        if fmt == "both":
            assert sum(n.endswith(".pdf") for n in names) == 5
        daily = next(n for n in names if "daily_record" in n and n.endswith(".docx"))
        text = "\n".join(p.text for p in Document(BytesIO(z.read(daily))).paragraphs)
    year = timezone.localdate().year + 543
    assert "๙๙/๒๕๖๙" not in text  # case number is not in the daily record
    assert str(year).translate(str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")) in text  # BE year, Thai digits
    assert Task.objects.filter(case=case, source="auto", title__startswith="ออกเอกสาร").count() == 5
    assert AuditLog.objects.filter(action="export", object_type="document").count() == 5
    page = client_inv.get(r["Location"]).content.decode()
    assert "ดาวน์โหลด ZIP" in page


@pytest.mark.django_db
def test_versions_increase_and_history_is_kept(client_inv, case, full_profile):
    for _ in range(2):
        client_inv.post(reverse("documents:issue", args=[case.pk]), {"doc": ["daily_record"], "format": "docx"})
    versions = list(
        IssuedDocument.objects.filter(case=case, key="daily_record").order_by("version").values_list("version", flat=True)
    )
    assert versions == [1, 2]
    d = IssuedDocument.objects.get(case=case, key="daily_record", version=2)
    assert len(d.template_version) == 12
    r = client_inv.get(reverse("documents:download", args=[case.pk, d.pk, "docx"]))
    assert r.status_code == 200 and AuditLog.objects.filter(action="download", object_type="document").exists()


@pytest.mark.django_db
def test_not_ready_document_is_skipped(client_inv, case, full_profile):
    client_inv.post(reverse("documents:issue", args=[case.pk]), {"doc": ["exhibits", "daily_record"], "format": "docx"})
    job = case.document_jobs.get()
    assert job.status == "done" and job.documents.count() == 1 and "บัญชีของกลาง" in job.error


@pytest.mark.django_db
def test_ai_draft_keeps_its_label_in_the_document(case, full_profile, investigator):
    acc = case.parties.get(role="accuser")
    Statement.objects.create(party=acc, version=2, text="ร่างจาก AI", source="ai", created_by=investigator)
    values = context.build(case)
    assert values["ผู้กล่าวหา"]["คำให้การ"].startswith("[ร่างโดย AI – รอตรวจ]")


@pytest.mark.django_db
def test_commander_cannot_issue(client, case, make_user):
    client.force_login(make_user(role="commander"))
    assert client.post(reverse("documents:issue", args=[case.pk]), {"doc": ["daily_record"]}).status_code == 403
    assert client.get(reverse("documents:page", args=[case.pk])).status_code == 200


@pytest.mark.django_db
def test_page_lists_groups_and_lock_reasons(client_inv, case):
    html = client_inv.get(reverse("documents:page", args=[case.pk])).content.decode()
    for text in ("รับแจ้ง", "สอบปากคำ", "ผู้ต้องหา", "ทรัพย์และของกลาง", "สรุปสำนวน", "ยังไม่พร้อม", "ชื่อหน่วย", "ทั้ง Word และ PDF"):
        assert text in html


@pytest.mark.django_db
def test_property_table_rows(case, full_profile):
    PropertyItem.objects.create(case=case, name="โทรศัพท์", value=5000, status="returned")
    values = context.build(case)
    data, missing = engine.fill(catalog.get("property_harmed").template_bytes(), values)
    doc = Document(BytesIO(data))
    rows = [[c.text for c in r.cells] for r in doc.tables[0].rows]
    assert len(rows) == 3 and rows[1][1] == "เงินสด" and rows[2][3] == "๕,๐๐๐.๐๐"
    assert "รวมมูลค่า ๑๗,๐๐๐.๐๐ บาท" in "\n".join(p.text for p in doc.paragraphs)
