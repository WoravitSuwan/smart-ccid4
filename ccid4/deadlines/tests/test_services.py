"""Phase 3: deadlines in the database, tasks, remand records, letters and timeline."""

from datetime import date, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cases.models import OutgoingLetter
from cases.tests.factories import CaseFactory, PartyFactory, PersonFactory
from core.convert import soffice_available
from deadlines import services
from deadlines.models import DeadlineRule, DeadlineRuleVersion, Detention, Holiday, Task
from deadlines.rules import TZ
from parties.models import Statement


@pytest.fixture
def case(investigator):
    return CaseFactory(owner=investigator, case_type="criminal", max_penalty_months=60, court_type="provincial")


@pytest.fixture
def suspect(case):
    arrived = timezone.now() - timedelta(hours=5)
    return PartyFactory(
        case=case, role="suspect", person=PersonFactory(first_name="ผู้ต้องหา"), arrived_at=arrived, arrested_at=arrived
    )


def keys(case):
    return [i.deadline.key for i in services.case_deadlines(case)]


# ---------------------------------------------------------------- rules in the database
@pytest.mark.django_db
def test_default_rules_are_seeded_with_version_history():
    rule = DeadlineRule.objects.get(key="remand_mid")
    assert rule.params["total_days"] == 48 and rule.version == 1
    assert DeadlineRuleVersion.objects.filter(rule=rule, version=1).exists()


@pytest.mark.django_db
def test_editing_a_rule_makes_a_new_version_and_is_used(admin_user):
    rule = DeadlineRule.objects.get(key="remand_mid")
    rule.params = rule.params | {"total_days": 40}
    rule.updated_by, rule.note = admin_user, "ทดสอบ"
    rule.save()
    assert rule.version == 2
    assert list(rule.versions.values_list("version", flat=True)) == [2, 1]
    assert services.load_rules().params["remand_mid"]["total_days"] == 40
    rule.name = "เปลี่ยนชื่ออย่างเดียว"
    rule.save()
    assert rule.version == 2  # name change alone is not a new rule version


@pytest.mark.django_db
def test_inactive_rule_falls_back_to_default():
    DeadlineRule.objects.filter(key="progress_1").update(active=False, params={"days": 5})
    assert services.load_rules().params["progress_1"]["days"] == 30


# ---------------------------------------------------------------- custody and remand (acceptance 4)
@pytest.mark.django_db
def test_acceptance_custody_then_remand_five_year_case(case, suspect, investigator):
    items = services.case_deadlines(case)
    custody = next(i for i in items if i.deadline.key == "custody")
    assert custody.status.text.startswith("เหลือ 42 ชม.") or custody.status.text.startswith("เหลือ 43 ชม.")
    start = timezone.localdate()
    services.record_detention(investigator, case, suspect, start, 12, court="ศาลอาญา")
    c = services.custody_for(suspect, case)
    assert c.rule_key == "remand_mid"
    assert (c.plan.used_times, c.plan.used_days, c.plan.remaining_times, c.plan.remaining_days) == (1, 12, 3, 36)
    assert c.plan.final_day == start + timedelta(days=47)
    assert "custody" not in keys(case) and "remand_mid" in keys(case)
    assert "เหลือ 3 ผัด 36 วัน" in c.deadline.label
    assert Task.objects.filter(case=case, source="auto", title__contains="ฝากขังผัดที่ 1").exists()
    assert AuditLog.objects.filter(action="create", object_type="detention").exists()


@pytest.mark.django_db
def test_remand_is_checked_against_the_rules(case, suspect, investigator):
    start = timezone.localdate()
    with pytest.raises(ValidationError):
        services.record_detention(investigator, case, suspect, start, 13)
    services.record_detention(investigator, case, suspect, start, 12)
    with pytest.raises(ValidationError, match="ทับ"):
        services.record_detention(investigator, case, suspect, start + timedelta(days=5), 12)
    with pytest.raises(ValidationError, match="ไม่ต่อเนื่อง"):
        services.record_detention(investigator, case, suspect, start + timedelta(days=20), 12)
    for i in range(1, 4):
        services.record_detention(investigator, case, suspect, start + timedelta(days=12 * i), 12)
    with pytest.raises(ValidationError, match="ครบ"):
        services.record_detention(investigator, case, suspect, start + timedelta(days=48), 1)
    assert Detention.objects.filter(party=suspect).count() == 4


@pytest.mark.django_db
def test_remand_needs_the_maximum_penalty(investigator):
    case = CaseFactory(owner=investigator, max_penalty_months=None, max_fine=None)
    sus = PartyFactory(case=case, role="suspect", arrived_at=timezone.now())
    assert services.custody_for(sus, case).missing_penalty
    with pytest.raises(ValidationError, match="โทษ"):
        services.record_detention(investigator, case, sus, timezone.localdate(), 7)


@pytest.mark.django_db
def test_minor_offence_one_seven_day_remand(investigator):
    case = CaseFactory(owner=investigator, max_penalty_months=3, max_fine=500)
    sus = PartyFactory(case=case, role="suspect", arrived_at=timezone.now())
    services.record_detention(investigator, case, sus, timezone.localdate(), 7)
    c = services.custody_for(sus, case)
    assert c.rule_key == "remand_minor" and c.plan.exhausted and "ครบอำนาจฝากขัง" in c.deadline.label


@pytest.mark.django_db
def test_magistrate_court_uses_delay_rules(investigator):
    case = CaseFactory(owner=investigator, court_type="magistrate", max_penalty_months=12)
    arrested = datetime(2026, 11, 14, 22, 30, tzinfo=TZ)
    sus = PartyFactory(case=case, role="suspect", arrested_at=arrested)
    c = services.custody_for(sus, case)
    assert c.kind == "delay" and c.deadline.key == "file_charge"
    assert c.deadline.due == datetime(2026, 11, 16, 22, 30, tzinfo=TZ)
    services.record_detention(investigator, case, sus, date(2026, 11, 16), 6)
    c = services.custody_for(sus, case)
    assert c.periods[0].kind == "delay" and c.plan.remaining_times == 4


@pytest.mark.django_db
def test_bail_stops_the_custody_clock(case):
    PartyFactory(case=case, role="suspect", arrived_at=timezone.now(), bail_status="ประกันตัวด้วยเงินสด")
    assert "custody" not in keys(case)


@pytest.mark.django_db
def test_holiday_in_database_moves_the_next_request(case, suspect, investigator):
    start = date(2026, 11, 10)
    services.record_detention(investigator, case, suspect, start, 12)  # ends Sat 21 Nov
    assert services.custody_for(suspect, case).plan.act_by == date(2026, 11, 20)
    Holiday.objects.create(date=date(2026, 11, 20), name="วันหยุดพิเศษ")
    assert services.custody_for(suspect, case).plan.act_by == date(2026, 11, 19)


# ---------------------------------------------------------------- other deadlines
@pytest.mark.django_db
def test_progress_notices_and_unknown_offender(case, investigator):
    case.dr_complaint_at = timezone.now() - timedelta(days=1)
    case.save()
    assert {"progress_1", "progress_2", "unknown_offender"} <= set(keys(case))
    services.sync_rule_tasks(case)
    t = Task.objects.get(case=case, rule_key="progress_1")
    services.set_done(t, investigator, True)
    assert "progress_1" not in keys(case)
    PartyFactory(case=case, role="suspect")
    assert "unknown_offender" not in keys(case)


@pytest.mark.django_db
def test_closed_case_has_no_deadlines(case, suspect):
    case.status = "closed"
    case.save()
    assert services.case_deadlines(case) == [] and case.nearest_deadline is None


# ---------------------------------------------------------------- tasks
@pytest.mark.django_db
def test_rule_tasks_are_created_ticked_and_removed(case, suspect, investigator):
    services.sync_rule_tasks(case)
    todo = set(Task.objects.filter(case=case, done_at__isnull=True).values_list("rule_key", flat=True))
    assert {"dr_complaint", "accuser_statement", "scene", f"suspect_statement:{suspect.pk}", f"rights:{suspect.pk}"} <= todo
    assert Task.objects.get(case=case, rule_key="penalty").done_at  # offense and penalty already filled
    Statement.objects.create(party=suspect, version=1, text="ให้การปฏิเสธ", created_by=investigator)
    services.sync_rule_tasks(case)
    st = Task.objects.get(case=case, rule_key=f"suspect_statement:{suspect.pk}")
    assert st.done_at and st.done_by is None
    Statement.objects.filter(party=suspect).delete()
    services.sync_rule_tasks(case)
    assert Task.objects.get(pk=st.pk).done_at is None  # the system ticked it, so it re-opens
    suspect.delete()
    services.sync_rule_tasks(case)
    assert not Task.objects.filter(case=case, rule_key__startswith="suspect_statement").exists()


@pytest.mark.django_db
def test_manual_tick_is_kept(case, investigator):
    services.sync_rule_tasks(case)
    t = Task.objects.get(case=case, rule_key="scene")
    services.set_done(t, investigator, True)
    services.sync_rule_tasks(case)
    t.refresh_from_db()
    assert t.done_at and t.done_by == investigator


@pytest.mark.django_db
def test_case_save_recomputes_tasks(client_inv, case):
    from cases.tests.test_case_page import page_data

    url = reverse("cases:detail", args=[case.pk])
    data = page_data(client_inv.get(url))
    data["c-dr_complaint_no"] = "15"
    client_inv.post(url, data)
    assert Task.objects.get(case=case, rule_key="dr_complaint").done_at


# ---------------------------------------------------------------- views
@pytest.mark.django_db
def test_task_toggle_returns_panel_and_log(client_inv, case):
    services.sync_rule_tasks(case)
    t = Task.objects.get(case=case, rule_key="scene")
    r = client_inv.post(reverse("deadlines:task_toggle", args=[case.pk, t.pk]), HTTP_HX_REQUEST="true")
    html = r.content.decode()
    assert r.status_code == 200 and 'id="side-panel"' in html and 'hx-swap-oob="true"' in html
    t.refresh_from_db()
    assert t.done_at


@pytest.mark.django_db
def test_add_and_delete_manual_task(client_inv, case):
    client_inv.post(reverse("deadlines:task_add", args=[case.pk]), {"title": "ได้รับผลลายนิ้วมือ", "done": "on"})
    t = Task.objects.get(case=case, title="ได้รับผลลายนิ้วมือ")
    assert t.source == "manual" and t.done_at
    rule = Task.objects.create(case=case, title="กติกา", source="rule", rule_key="x")
    assert client_inv.post(reverse("deadlines:task_delete", args=[case.pk, rule.pk])).status_code == 404
    client_inv.post(reverse("deadlines:task_delete", args=[case.pk, t.pk]))
    assert not Task.objects.filter(pk=t.pk).exists()


@pytest.mark.django_db
def test_commander_cannot_change_tasks_or_remand(client, case, suspect, make_user):
    client.force_login(make_user(role="commander"))
    services.sync_rule_tasks(case)
    t = Task.objects.filter(case=case).first()
    assert client.post(reverse("deadlines:task_toggle", args=[case.pk, t.pk])).status_code == 403
    url = reverse("deadlines:detention_add", args=[case.pk, suspect.pk])
    assert client.post(url, {f"det{suspect.pk}-start_date": "2026-11-16", f"det{suspect.pk}-days": "12"}).status_code == 403


@pytest.mark.django_db
def test_detention_view_and_case_page(client_inv, case, suspect):
    p = f"det{suspect.pk}"
    url = reverse("deadlines:detention_add", args=[case.pk, suspect.pk])
    r = client_inv.post(url, {f"{p}-start_date": timezone.localdate().isoformat(), f"{p}-days": "12"}, follow=True)
    assert "บันทึกแล้ว" in r.content.decode()
    assert Detention.objects.filter(party=suspect).count() == 1
    html = client_inv.get(reverse("cases:detail", args=[case.pk])).content.decode()
    assert "ฝากขังผัด 1" in html and "เหลือ" in html and "บันทึกฝากขังผัดที่ ๒" in html
    det = Detention.objects.get()
    client_inv.post(reverse("deadlines:detention_delete", args=[case.pk, det.pk]))
    assert not Detention.objects.exists()


@pytest.mark.django_db
def test_list_page_shows_nearest_deadline(client_inv, case, suspect):
    html = client_inv.get(reverse("cases:list")).content.decode()
    assert "ควบคุมตัว 48 ชม." in html or "แจ้งความคืบหน้า" in html


# ---------------------------------------------------------------- letters
@pytest.mark.django_db
def test_letter_draft_issue_reply(client_inv, investigator, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    case = CaseFactory(owner=investigator, case_type="online")
    services.sync_rule_tasks(case)
    assert not Task.objects.get(case=case, rule_key="bank_letter").done_at
    r = client_inv.post(
        reverse("cases:letter_new", args=[case.pk]) + "?t=bank",
        {
            "t": "bank",
            "subject": "ขอข้อมูลบัญชี",
            "recipient": "ผู้จัดการธนาคาร",
            "f_bank": "ธนาคารสมมติ",
            "f_account_no": "123-4-56789-0",
            "f_from_date": "2026-11-01",
        },
    )
    assert r.status_code == 302
    letter = OutgoingLetter.objects.get(case=case)
    assert letter.status == "draft" and letter.fields["bank"] == "ธนาคารสมมติ" and letter.fields["from_date"] == "2026-11-01"
    assert Task.objects.get(case=case, rule_key="bank_letter").done_at
    assert Task.objects.filter(case=case, source="auto", title__startswith="ร่างหนังสือ").exists()
    edit = reverse("cases:letter_edit", args=[case.pk, letter.pk])
    client_inv.post(edit, {"action": "issue"})
    letter.refresh_from_db()
    assert letter.status == "issued" and letter.number.endswith(f"/1/{timezone.localdate().year + 543}") and letter.issued_at
    pdf = SimpleUploadedFile("reply.pdf", b"%PDF-1.4 test", content_type="application/pdf")
    client_inv.post(edit, {"action": "reply", "replied_at": "2026-11-20", "reply_note": "ได้รายการเดินบัญชี", "reply_file": pdf})
    letter.refresh_from_db()
    assert letter.status == "replied" and letter.reply_file
    r = client_inv.get(reverse("cases:letter_reply_file", args=[case.pk, letter.pk]))
    assert r.status_code == 200 and AuditLog.objects.filter(action="download", object_type="letter").exists()


@pytest.mark.django_db
def test_letter_reply_rejects_other_file_types(client_inv, investigator):
    case = CaseFactory(owner=investigator)
    letter = OutgoingLetter.objects.create(
        case=case, template="cctv", subject="s", recipient="r", status="issued", created_by=investigator
    )
    exe = SimpleUploadedFile("x.exe", b"MZ", content_type="application/octet-stream")
    client_inv.post(
        reverse("cases:letter_edit", args=[case.pk, letter.pk]),
        {"action": "reply", "replied_at": "2026-11-20", "reply_file": exe},
    )
    letter.refresh_from_db()
    assert letter.status == "issued"


# ---------------------------------------------------------------- timeline
@pytest.mark.django_db
def test_timeline_lists_events_in_order(client_inv, case, suspect, investigator):
    services.record_detention(investigator, case, suspect, timezone.localdate(), 12)
    r = client_inv.get(reverse("cases:timeline", args=[case.pk]))
    items = r.context["items"]
    kinds = [e.kind for e in items]
    assert "report" in kinds and "arrest" in kinds and "detention" in kinds
    assert [e.when for e in items if not e.all_day] == sorted(e.when for e in items if not e.all_day)
    assert kinds.index("arrest") < kinds.index("detention")


@pytest.mark.django_db
def test_timeline_export_is_audited(client_inv, case):
    r = client_inv.get(reverse("cases:timeline", args=[case.pk]) + "?format=docx")
    assert r.status_code == 200 and r["Content-Type"].startswith("application/vnd.openxmlformats")
    assert AuditLog.objects.filter(action="export", object_type="case").exists()


@pytest.mark.django_db
@pytest.mark.skipif(not soffice_available(), reason="LibreOffice not installed")
def test_timeline_pdf(client_inv, case):
    r = client_inv.get(reverse("cases:timeline", args=[case.pk]) + "?format=pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
