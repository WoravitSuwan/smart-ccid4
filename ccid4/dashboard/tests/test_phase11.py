"""Phase 11: แดชบอร์ด (B7) · แจ้งเตือนและติดตามของผู้แจ้ง (B8) · บัตรประชาชน (B9). Fictional data only."""

import io
import shutil
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cases.models import Case
from cases.tests.factories import CaseFactory, PartyFactory, PersonFactory
from core.models import Notification
from cyber import money
from dashboard import alerts, services
from dashboard.models import DeadlineAlert, DigestSetting
from deadlines.models import Task
from intake import tracking
from parties.models import Statement


@pytest.fixture
def team(make_user):
    head = make_user(role="supervisor", username="head", unit_group="กก.1")
    a = make_user(role="investigator", username="inv_a", unit_group="กก.1")
    b = make_user(role="investigator", username="inv_b", unit_group="กก.1")
    return head, a, b


def overdue_case(owner, **kw):
    """Reported 40 days ago, progress report (30 days) not done → overdue."""
    at = timezone.now() - timedelta(days=40)
    return CaseFactory(owner=owner, reported_at=at, dr_complaint_at=at, **kw)


def arrested_case(owner, hours_ago=5):
    c = CaseFactory(owner=owner, reported_at=timezone.now())
    PartyFactory(
        case=c,
        role="suspect",
        person=PersonFactory(citizen_id="1100000000070"),
        arrived_at=timezone.now() - timedelta(hours=hours_ago),
    )
    return c


# ---------------------------------------------------------------- B7 personal
@pytest.mark.django_db
def test_personal_dashboard(client_inv, investigator):
    late = overdue_case(investigator, case_no="501/2569")
    soon = arrested_case(investigator)
    Case.objects.filter(pk=late.pk).update(updated_at=timezone.now() - timedelta(days=45))
    p = PartyFactory(case=soon, role="witness", person=PersonFactory(citizen_id="1100000000088"))
    Statement.objects.create(party=p, version=1, text="ร่าง", source=Statement.Source.AI, created_by=investigator)
    Task.objects.create(case=soon, title="ตรวจสถานที่เกิดเหตุ (สมมติ)", source=Task.Source.MANUAL, created_by=investigator)
    data = services.personal(investigator)
    assert {c.pk for c, _ in data["overdue"]} == {late.pk} and soon.pk in {c.pk for c, _ in data["week"]}
    assert [c.pk for c in data["stale"]] == [late.pk] and data["review_count"] == 1 and data["tasks"]
    html = client_inv.get(reverse("core:home")).content.decode()
    for text in (
        "501/2569",
        "เลยกำหนด",
        "กำหนดเวลาใน 7 วัน",
        "ตรวจสถานที่เกิดเหตุ (สมมติ)",
        "ร่างจาก AI รอตรวจ",
        "ไม่มีความเคลื่อนไหวเกิน 30 วัน",
    ):
        assert text in html


# ---------------------------------------------------------------- B7 unit
@pytest.mark.django_db
def test_unit_dashboard_scope_stats_and_mules(team, client, make_user):
    head, a, b = team
    outsider = make_user(role="investigator", username="other", unit_group="กก.2")
    c1 = overdue_case(a, offense="ฉ้อโกงประชาชน", damage_amount=Decimal("15000"))
    c2 = CaseFactory(owner=b, offense="ฉ้อโกงประชาชน")
    CaseFactory(owner=outsider, offense="ลักทรัพย์")
    money.add_transfer(c1, a, to_no="123-4-56789-0", to_bank="กสิกรไทย", amount="5000")
    money.add_transfer(c2, b, to_no="1234567890", to_bank="กสิกรไทย", amount="7000")
    data = services.unit(head)
    assert data["total"] == 2  # head sees the group only
    by_name = {o.name: o for o in data["officers"]}
    assert by_name[a.display_name].overdue >= 1 and by_name[b.display_name].open == 1
    assert (
        data["mules"][0]["cases"] == 2
        and data["mules"][0]["received"] == Decimal("12000")
        and data["mules"][0]["masked"] == "xxx-x-x6789-x"
    )
    # the offense chart counts the chosen month (c1 was reported 40 days ago = last month)
    assert (
        data["offense_scope"] == "ในเดือนนี้"
        and dict(data["offenses"]) == {"ฉ้อโกงประชาชน": 1}
        and data["damage_all"] >= Decimal("15000")
    )
    client.force_login(head)
    html = client.get(reverse("dashboard:unit")).content.decode()
    assert "รายพนักงานสอบสวน" in html and "xxx-x-x6789-x" in html and 'class="chart"' in html and "data-tip" in html
    r = client.get(reverse("dashboard:export", args=["xlsx"]))
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(r.content))
    assert {"สรุป", "รายพนักงานสอบสวน", "ฐานความผิด", "บัญชีม้าที่พบซ้ำ"} <= set(wb.sheetnames)
    assert AuditLog.objects.filter(action="export", object_type="dashboard").exists()
    client.force_login(a)
    assert client.get(reverse("dashboard:unit")).status_code == 403


@pytest.mark.django_db
@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_monthly_report_pdf(team, client):
    head, a, _ = team
    overdue_case(a)
    client.force_login(head)
    r = client.get(reverse("dashboard:export", args=["pdf"]))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


# ---------------------------------------------------------------- B8 alerts, digest, channels, transfer
@pytest.mark.django_db
def test_deadline_alerts_once_per_stage(investigator):
    late = overdue_case(investigator)
    near = arrested_case(investigator, hours_ago=30)  # < 24 h left of 48 h → "ครบกำหนดวันนี้" stage
    assert alerts.stage_for(3) == "d3" and alerts.stage_for(2) is None and alerts.stage_for(-1) == "over"
    first = alerts.run_alerts()
    assert first >= 2 and alerts.run_alerts() == 0
    assert (
        DeadlineAlert.objects.filter(case=late, stage="over").exists()
        and DeadlineAlert.objects.filter(case=near, stage="d0").exists()
    )
    titles = list(Notification.objects.filter(user=investigator, kind="deadline").values_list("title", flat=True))
    assert any(t.startswith("เลยกำหนด") for t in titles) and any(t.startswith("ครบกำหนดวันนี้") for t in titles)


@pytest.mark.django_db
def test_daily_digest_is_opt_in_and_has_no_personal_data(investigator, settings):
    c = overdue_case(investigator, case_no="777/2569")
    PartyFactory(case=c, role="accuser", person=PersonFactory(first_name="ชื่อลับ", citizen_id="1100000000096"))
    investigator.email = "inv@unit.local"
    investigator.save()
    DigestSetting.objects.create(user=investigator, enabled=True, email="inv@unit.local")
    settings.DAILY_DIGEST = False
    assert alerts.send_digests() == 0 and not mail.outbox
    settings.DAILY_DIGEST = True
    assert alerts.send_digests() == 1 and alerts.send_digests() == 0  # once a day
    body = mail.outbox[0].body
    assert "777/2569" in body and "ชื่อลับ" not in body and "1100000000096" not in body


@pytest.mark.django_db
def test_external_channel_is_off_unless_approved(investigator, settings, monkeypatch):
    import urllib.request

    from core.notify import notify

    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: calls.append(req) or io.BytesIO(b""))
    notify([investigator], "test", "มีเรื่องใหม่", "รายละเอียดคดีลับ")
    assert calls == []
    settings.EXTERNAL_CHANNELS, settings.EXTERNAL_WEBHOOK_URL = ["webhook"], "http://chat.unit.local/hook"
    notify([investigator], "test", "มีเรื่องใหม่", "รายละเอียดคดีลับ")
    assert len(calls) == 1 and b"\xe0\xb8" in calls[0].data and "รายละเอียดคดีลับ".encode() not in calls[0].data


@pytest.mark.django_db
def test_case_transfer_notifies_both_owners(team, client):
    head, a, b = team
    c = CaseFactory(owner=a, case_no="600/2569")
    client.force_login(a)
    assert client.post(reverse("cases:transfer", args=[c.pk]), {"owner": b.pk, "reason": "แบ่งงานใหม่"}).status_code == 403
    client.force_login(head)
    html = client.get(reverse("cases:detail", args=[c.pk])).content.decode()
    assert "โอนคดี" in html
    client.post(reverse("cases:transfer", args=[c.pk]), {"owner": b.pk, "reason": "แบ่งงานตามความเชี่ยวชาญ"})
    c.refresh_from_db()
    assert c.owner == b
    assert (
        Notification.objects.filter(user=b, kind="case_transfer").exists()
        and Notification.objects.filter(user=a, kind="case_transfer").exists()
    )
    assert c.tasks.filter(title__contains="โอนคดี").exists() and AuditLog.objects.filter(detail__contains="โอนคดี").exists()


@pytest.mark.django_db
def test_tracking_link_shows_only_published_items_and_expires(investigator, client_inv, client):
    c = CaseFactory(owner=investigator, receipt_no="L123")
    PartyFactory(case=c, role="accuser", person=PersonFactory(first_name="ผู้แจ้งลับ", citizen_id="1100000000100"))
    Task.objects.create(
        case=c, title="ภายใน: ตรวจสอบบัญชีปลายทาง", source=Task.Source.MANUAL, done_at=timezone.now(), created_by=investigator
    )
    client_inv.post(reverse("intake:tracking_new", args=[c.pk]), {"days": "30"})
    link = tracking.current(c)
    assert link and link.reference == "L123" and (link.expires_at - timezone.now()).days in (29, 30)
    client_inv.post(reverse("intake:tracking_message", args=[c.pk]), {"text": "ได้รับข้อมูลจากธนาคารแล้ว อยู่ระหว่างติดตามตัวผู้กระทำผิด"})
    html = client.get(f"/t/{link.token}/").content.decode()
    assert "ได้รับข้อมูลจากธนาคารแล้ว" in html and "ภายใน: ตรวจสอบบัญชีปลายทาง" not in html and "ผู้แจ้งลับ" not in html
    assert client_inv.get(reverse("intake:tracking_qr", args=[c.pk]))["Content-Type"] == "image/png"
    client_inv.post(reverse("intake:tracking_revoke", args=[c.pk]))
    assert client.get(f"/t/{link.token}/").status_code == 410
    fresh = tracking.create(c, investigator, 1)
    type(fresh).objects.filter(pk=fresh.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert client.get(f"/t/{fresh.token}/").status_code == 410
    assert client.get("/t/no-such-token/").status_code == 404


# ---------------------------------------------------------------- B9
@pytest.mark.django_db
def test_card_ocr_review_and_audit(investigator, client_inv):
    c = CaseFactory(owner=investigator)
    PartyFactory(case=c, role="accuser", person=PersonFactory(citizen_id="1100000000118"))
    html = client_inv.get(reverse("cases:detail", args=[c.pk])).content.decode()
    assert "อ่านบัตรประชาชน" in html and 'id="card-dialog"' in html and "ws://127.0.0.1:8765" in html
    resp = client_inv.get(reverse("cases:detail", args=[c.pk]))
    assert "ws://127.0.0.1:8765" in resp["Content-Security-Policy"]
    card = ("บัตรประจำตัวประชาชน Thai National ID Card เลขประจำตัวประชาชน 1 1017 00203 45 0 "
            "ชื่อตัวและชื่อสกุล นางสาว ทดลอง ใจดี เกิดวันที่ 14 ก.พ. 2535 "
            "ที่อยู่ 99/1 หมู่ที่ 4 ตำบลบางรัก อำเภอเมืองสมมติ จังหวัดสมมติ วันออกบัตร 1 ม.ค. 2566")  # fmt: skip
    r = client_inv.post(
        reverse("cases:card_ocr", args=[c.pk]), {"image": SimpleUploadedFile("card.txt", card.encode(), "text/plain")}
    )
    d = r.json()["data"]
    assert (
        d["citizen_id"] == "1101700203450" and d["cid_valid"] and d["first_name"] == "ทดลอง" and d["birth_date"] == "1992-02-14"
    )
    assert d["moo"] == "4" and d["province"] == "สมมติ"
    client_inv.post(
        reverse("cases:card_audit", args=[c.pk]), {"source": "reader", "name": "นางสาวทดลอง ใจดี", "cid": "1101700203450"}
    )
    e = AuditLog.objects.filter(action="import", object_type="person").latest("id")
    assert "เครื่องอ่านบัตร" in e.detail and "1101700203450" not in e.detail and "1-xxxx-xxxxx-45-0" in e.detail
