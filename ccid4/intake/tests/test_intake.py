"""Phase 6: links/QR, public chat and form, staff mode, processing into cases, tracking, arrest-record import."""

from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from ai import llm
from audit.models import AuditLog
from cases.models import Case
from intake import interview, services
from intake.models import ArrestImport, IntakeLink, IntakeSubmission

SAMPLE_ARREST = Path(__file__).resolve().parents[2] / "sample_data" / "mock_arrest_record.pdf"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

CHAT_ANSWERS = [
    "นางสาวสมหญิง ทดสอบ 0812345678 ที่อยู่ 12 ม.3 ต.บางพูด อ.ปากเกร็ด จ.นนทบุรี",
    "ถูกหลอกซื้อโทรศัพท์ทางเฟซบุ๊ก โอนเงินแล้วไม่ได้ของ ผู้ขายบล็อกไป",
    "เมื่อวานตอนเย็น ทางเฟซบุ๊กกลุ่มซื้อขายมือถือ",
    "เงิน 15,900 บาท",
    "กสิกรไทย 123-4-56789-0 ชื่อนายเอ 15900 บาท",
    "นายบี สมมติ โทร 0899999999",
    "ไม่มี",
]


@pytest.fixture
def link(investigator):
    return services.link_for(investigator)


def run_chat(client, link, kind="online"):
    client.get(reverse("intake_public:chat", args=[link.token, kind]))
    send = reverse("intake_public:chat_send", args=[link.token, kind])
    for text in CHAT_ANSWERS[: len(interview.topics_for(kind))]:
        r = client.post(send, {"text": text})
        assert r.status_code == 200
    return r


# ---------------------------------------------------------------- links
@pytest.mark.django_db
def test_link_is_personal_and_can_be_replaced(client, investigator):
    a = services.link_for(investigator)
    assert services.link_for(investigator) == a
    b = services.regenerate_link(investigator)
    assert a.token != b.token and not IntakeLink.objects.get(pk=a.pk).active
    assert client.get(reverse("intake_public:start", args=[a.token])).status_code == 404
    assert client.get(reverse("intake_public:start", args=[b.token])).status_code == 200


@pytest.mark.django_db
def test_officer_page_qr_and_poster(client_inv, link):
    html = client_inv.get(reverse("intake:index")).content.decode()
    assert link.token in html and "<svg" in html and "กรอกแทนผู้แจ้ง" in html and "พิมพ์ป้าย A5" in html
    png = client_inv.get(reverse("intake:qr_png", args=["online"]))
    assert png["Content-Type"] == "image/png" and png.content.startswith(b"\x89PNG")
    assert "@page" not in client_inv.get(reverse("intake:poster", args=["criminal"])).content.decode()  # styles come from app.css
    assert client_inv.get(reverse("intake:poster", args=["nope"])).status_code == 404


@pytest.mark.django_db
def test_qr_uses_public_address_when_configured(client_inv, link, settings):
    settings.PUBLIC_BASE_URL = "https://report.unit.example"
    assert f"https://report.unit.example/r/{link.token}/?kind=online" in client_inv.get(reverse("intake:index")).content.decode()


# ---------------------------------------------------------------- public chat (acceptance 1)
@pytest.mark.django_db
def test_chat_report_becomes_a_case_for_the_link_owner(
    client, client_inv, link, investigator, django_capture_on_commit_callbacks
):
    public = client.__class__()  # an anonymous citizen
    r = run_chat(public, link)
    assert "ได้ข้อมูลครบแล้ว" in r.content.decode()
    submit = reverse("intake_public:chat_submit", args=[link.token, "online"])
    r = public.post(submit, {"files": [SimpleUploadedFile("slip.png", PNG, "image/png")]})
    assert r.status_code == 302 and not IntakeSubmission.objects.exists()  # no consent yet
    with django_capture_on_commit_callbacks(execute=True):
        r = public.post(submit, {"consent": "on", "files": [SimpleUploadedFile("slip.png", PNG, "image/png")]})
    sub = IntakeSubmission.objects.get()
    assert r["Location"] == reverse("intake_public:done", args=[sub.track_token])
    assert sub.status == "case_created" and sub.mode == "chat" and sub.files.count() == 1 and len(sub.files.get().sha256) == 64
    case = sub.case
    assert (
        case.owner == investigator
        and case.case_type == "online"
        and case.status == "received"
        and case.receipt_no == sub.reference
    )
    acc = case.parties.get(role="accuser").person
    assert (acc.title, acc.first_name, acc.last_name, acc.phone) == ("นางสาว", "สมหญิง", "ทดสอบ", "0812345678")
    assert "บางพูด" in acc.address
    assert case.parties.get(role="suspect").person.first_name == "บี"
    assert case.properties.get().value == 15900 and case.damage_amount == 15900
    assert "1234567890" in case.circumstances and "กสิกรไทย" in case.circumstances
    assert case.tasks.filter(source="auto", title__contains=sub.reference).exists()
    html = client_inv.get(reverse("cases:detail", args=[case.pk])).content.decode()
    assert "ถูกหลอกซื้อโทรศัพท์ทางเฟซบุ๊ก" in html and sub.reference in html and "bubble-user" in html  # chat history in the accuser tab


@pytest.mark.django_db
def test_chat_reset_and_message_limits(client, link):
    client.get(reverse("intake_public:chat", args=[link.token, "criminal"]))
    client.post(reverse("intake_public:chat_send", args=[link.token, "criminal"]), {"text": "ก" * 5000})
    msgs = client.session[f"intake:{link.token}:criminal"]
    assert len(msgs[1]["text"]) == 2000
    client.post(reverse("intake_public:chat_reset", args=[link.token, "criminal"]))
    assert len(client.get(reverse("intake_public:chat", args=[link.token, "criminal"])).context["messages_"]) == 1


@pytest.mark.django_db
def test_incomplete_chat_cannot_be_submitted(client, link):
    client.get(reverse("intake_public:chat", args=[link.token, "online"]))
    client.post(reverse("intake_public:chat_submit", args=[link.token, "online"]), {"consent": "on"})
    assert not IntakeSubmission.objects.exists()


# ---------------------------------------------------------------- step form
def form_data(**extra):
    return {
        "reporter-name": "นายสมชาย ใจดี", "reporter-phone": "0811111111", "reporter-address": "1 ถ.สุขุมวิท",
        "story-story": "มีคนขับรถชนท้ายแล้วหนีไป", "when_where-when": "2026-11-15T18:30", "when_where-where": "ถนนพระราม 2",
        "loss-items": "กันชนรถ", "loss-amount": "8000", "consent": "on",
    } | extra  # fmt: skip


@pytest.mark.django_db
def test_step_form_report(client, link, django_capture_on_commit_callbacks):
    url = reverse("intake_public:form", args=[link.token, "criminal"])
    r = client.post(url, form_data(**{"reporter-name": ""}))
    assert r.status_code == 200 and "ชื่อ-นามสกุล" in r.content.decode() and not IntakeSubmission.objects.exists()
    with django_capture_on_commit_callbacks(execute=True):
        client.post(url, form_data())
    case = IntakeSubmission.objects.get().case
    assert case.case_type == "criminal" and "ขับรถ" in case.offense
    assert timezone.localtime(case.incident_from).strftime("%Y-%m-%d %H:%M") == "2026-11-15 18:30"
    assert case.properties.get().name == "กันชนรถ"


@pytest.mark.django_db
def test_bad_files_are_refused(client, link, settings):
    url = reverse("intake_public:form", args=[link.token, "criminal"])
    fake = SimpleUploadedFile("photo.jpg", b"MZ\x90\x00not an image", "image/jpeg")
    client.post(url, form_data(files=[fake]))
    assert IntakeSubmission.objects.get().files.count() == 0
    settings.INTAKE_MAX_FILES = 1
    good, errors = services.check_files([SimpleUploadedFile(f"{i}.png", PNG, "image/png") for i in range(3)])
    assert len(good) == 1 and errors


@pytest.mark.django_db
def test_submissions_are_rate_limited(client, link, settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "rl-test"}}
    url = reverse("intake_public:form", args=[link.token, "criminal"])
    for _ in range(7):
        client.post(url, form_data())
    assert IntakeSubmission.objects.count() == 5


# ---------------------------------------------------------------- staff mode, tracking, failures
@pytest.mark.django_db
def test_staff_fills_in_for_the_reporter(client_inv, investigator, django_capture_on_commit_callbacks):
    url = reverse("intake:staff_form", args=["criminal"])
    assert "ข้อความยินยอม" in client_inv.get(url).content.decode()
    data = form_data()
    data.pop("consent")
    with django_capture_on_commit_callbacks(execute=True):
        r = client_inv.post(url, data)
    assert r["Location"] == reverse("intake:index")
    sub = IntakeSubmission.objects.get()
    assert sub.staff == investigator and sub.consent_text == interview.PDPA_STAFF_TEXT and sub.case.owner == investigator
    assert AuditLog.objects.filter(action="create", object_type="intake").exists()


@pytest.mark.django_db
def test_staff_mode_needs_login(client):
    assert client.get(reverse("intake:staff_form", args=["criminal"])).status_code == 302


@pytest.mark.django_db
def test_tracking_page_shows_status_only(client, link, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("intake_public:form", args=[link.token, "criminal"]), form_data())
    sub = IntakeSubmission.objects.get()
    t = sub.case.tasks.create(title="สอบปากคำผู้แจ้งแล้ว", source="manual", done_at=timezone.now(), show_to_complainant=True)
    sub.case.tasks.create(title="ภายใน: ตรวจประวัติผู้ต้องสงสัย", source="manual", done_at=timezone.now())
    html = client.get(reverse("intake_public:track", args=[sub.track_token])).content.decode()
    assert sub.reference in html and t.title in html and "ภายใน" not in html and "ขับรถ" not in html
    assert client.get(reverse("intake_public:track", args=["wrong-token"])).status_code == 404


@pytest.mark.django_db
def test_failed_processing_can_be_retried(client, client_inv, link, monkeypatch, django_capture_on_commit_callbacks):
    def boom(*a, **k):
        raise llm.AIError("ติดต่อเครื่อง AI ของหน่วยไม่ได้")

    monkeypatch.setattr(llm, "complete_json", boom)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("intake_public:form", args=[link.token, "criminal"]), form_data())
    sub = IntakeSubmission.objects.get()
    assert sub.status == "failed" and not Case.objects.exists()
    monkeypatch.undo()
    client_inv.post(reverse("intake:retry", args=[sub.pk]))
    sub.refresh_from_db()
    assert sub.status == "case_created" and sub.case


@pytest.mark.django_db
def test_attachment_download_is_owner_only_and_audited(client, client_inv, link, make_user, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse("intake_public:form", args=[link.token, "criminal"]),
            form_data(files=[SimpleUploadedFile("a.png", PNG, "image/png")]),
        )
    f = IntakeSubmission.objects.get().files.get()
    assert client_inv.get(reverse("intake:file", args=[f.pk])).status_code == 200
    assert AuditLog.objects.filter(action="download", object_type="intake_file").exists()
    other = client.__class__()
    other.force_login(make_user(role="investigator"))
    assert other.get(reverse("intake:file", args=[f.pk])).status_code == 403


# ---------------------------------------------------------------- import from an arrest record (A2.3)
@pytest.mark.django_db
def test_import_arrest_record_review_and_create(client_inv, investigator):
    upload = SimpleUploadedFile("arrest.pdf", SAMPLE_ARREST.read_bytes(), "application/pdf")
    r = client_inv.post(reverse("intake:import_upload"), {"file": upload, "source": "arrest"})
    imp = ArrestImport.objects.get()
    assert r["Location"] == reverse("intake:import_review", args=[imp.pk]) and imp.status == "review"
    page = client_inv.get(r["Location"])
    html = page.content.decode()
    assert "นายทดสอบ ระบบดี" in html and "ร่างโดย AI – รอตรวจ" in html and imp.sha256 in html
    data = {k: v for k, v in page.context["form"].initial.items()}
    data.update({"s-TOTAL_FORMS": "1", "s-INITIAL_FORMS": "0", "s-MIN_NUM_FORMS": "0", "s-MAX_NUM_FORMS": "1000"})
    for k, v in page.context["fs"].initial[0].items():
        data[f"s-0-{k}"] = v
    r = client_inv.post(reverse("intake:import_review", args=[imp.pk]), data)
    case = Case.objects.get()
    assert r["Location"] == case.get_absolute_url() and case.status == "imported" and case.owner == investigator
    sus = case.parties.get(role="suspect")
    assert sus.person.citizen_id == "1100900123456" and timezone.localtime(sus.arrested_at).hour == 21
    assert "สมุดบัญชีเงินฝาก" in case.circumstances and not case.properties.exists()  # exhibits are not harmed property
    assert case.parties.get(role="accuser").person.title == "ร.ต.อ."


@pytest.mark.django_db
def test_import_file_preview_is_private_and_frameable(client_inv, investigator, make_user, client):
    upload = SimpleUploadedFile("arrest.pdf", SAMPLE_ARREST.read_bytes(), "application/pdf")
    client_inv.post(reverse("intake:import_upload"), {"file": upload, "source": "arrest"})
    imp = ArrestImport.objects.get()
    r = client_inv.get(reverse("intake:import_file", args=[imp.pk]))
    assert r["X-Frame-Options"] == "SAMEORIGIN" and "frame-ancestors 'self'" in r["Content-Security-Policy"]
    client.force_login(make_user(role="investigator"))
    assert client.get(reverse("intake:import_file", args=[imp.pk])).status_code == 404


@pytest.mark.django_db
def test_image_import_without_ocr_model_lets_officer_type(client_inv):
    r = client_inv.post(reverse("intake:import_upload"), {"file": SimpleUploadedFile("a.png", PNG, "image/png")}, follow=True)
    imp = ArrestImport.objects.get()
    assert imp.status == "failed" and "OCR" in imp.error and "กรอกข้อมูลเองในหน้าตรวจทานได้" in r.content.decode()


def test_ocr_cleans_stray_spaces_but_keeps_words():
    from ai.ocr import clean_thai

    assert clean_thai("บั นทึกการจั บกุม นายสมมติ ทดลองดี") == "บันทึกการจับกุม นายสมมติ ทดลองดี"


@pytest.mark.django_db
def test_officer_publishes_progress_to_tracking_page(client, client_inv, link, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("intake_public:form", args=[link.token, "criminal"]), form_data())
    sub = IntakeSubmission.objects.get()
    t = sub.case.tasks.create(title="ได้รับภาพกล้องวงจรปิดแล้ว", source="manual", done_at=timezone.now())
    track = reverse("intake_public:track", args=[sub.track_token])
    assert t.title not in client.get(track).content.decode()
    r = client_inv.post(reverse("deadlines:task_share", args=[sub.case.pk, t.pk]), HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    assert t.title in client.get(track).content.decode()
