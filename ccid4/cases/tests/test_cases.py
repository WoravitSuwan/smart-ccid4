import pytest
from django.urls import reverse

from audit.models import AuditLog
from cases.models import Case
from cases.tests.factories import CaseFactory, PartyFactory


@pytest.mark.django_db
def test_investigator_sees_only_own_and_shared(client_inv, investigator, make_user):
    mine = CaseFactory(owner=investigator, case_no="1/2569")
    other = CaseFactory(owner=make_user(), case_no="2/2569")
    shared = CaseFactory(owner=make_user(), case_no="3/2569")
    shared.shared_with.add(investigator)
    html = client_inv.get(reverse("cases:list")).content.decode()
    assert "1/2569" in html and "3/2569" in html and "2/2569" not in html
    assert client_inv.get(reverse("cases:detail", args=[other.pk])).status_code == 404
    assert client_inv.get(reverse("cases:detail", args=[mine.pk])).status_code == 200


@pytest.mark.django_db
def test_supervisor_sees_group_commander_sees_all(client, make_user):
    a = make_user(unit_group="กก.1")
    b = make_user(unit_group="กก.2")
    CaseFactory(owner=a, case_no="10/2569")
    CaseFactory(owner=b, case_no="20/2569")
    sup = make_user(role="supervisor", unit_group="กก.1")
    client.force_login(sup)
    html = client.get(reverse("cases:list")).content.decode()
    assert "10/2569" in html and "20/2569" not in html
    client.force_login(make_user(role="commander"))
    html = client.get(reverse("cases:list")).content.decode()
    assert "10/2569" in html and "20/2569" in html


@pytest.mark.django_db
def test_cards_filters_and_type_tabs(client_inv, investigator):
    CaseFactory(owner=investigator, status="received", case_type="criminal", case_no="", receipt_no="R-1", offense="ลักทรัพย์")
    CaseFactory(owner=investigator, status="closed", case_type="online", case_no="5/2569")
    PartyFactory(case=CaseFactory(owner=investigator, case_no="7/2569"), person__first_name="ปรีชา")
    r = client_inv.get(reverse("cases:list"))
    assert r.context["counts"] == {"total": 3, "received": 1, "investigating": 1, "closed": 1}
    assert [c.label for c in client_inv.get(reverse("cases:list") + "?status=closed").context["page"]] == ["5/2569"]
    assert [c.label for c in client_inv.get(reverse("cases:list") + "?type=criminal").context["page"]] == ["R-1"]
    assert [c.label for c in client_inv.get(reverse("cases:list") + "?q=ปรีชา").context["page"]] == ["7/2569"]
    partial = client_inv.get(reverse("cases:list") + "?q=ลักทรัพย์", HTTP_HX_REQUEST="true")
    assert "<table" in partial.content.decode() and "<html" not in partial.content.decode()


@pytest.mark.django_db
def test_soft_delete_needs_confirmation_and_is_audited(client_inv, investigator):
    c = CaseFactory(owner=investigator, case_no="9/2569")
    client_inv.post(reverse("cases:delete", args=[c.pk]), {"confirm": "ผิด", "reason": "x"})
    assert Case.objects.alive().filter(pk=c.pk).exists()
    client_inv.post(reverse("cases:delete", args=[c.pk]), {"confirm": "9/2569", "reason": "เปิดซ้ำ"})
    c.refresh_from_db()
    assert c.deleted_at and c.deleted_by == investigator
    assert c not in client_inv.get(reverse("cases:list")).context["page"].object_list
    assert AuditLog.objects.filter(action="delete", object_type="case", object_id=str(c.pk), detail__contains="เปิดซ้ำ").exists()


@pytest.mark.django_db
def test_create_case_is_owned_and_audited(client_inv, investigator):
    r = client_inv.post(reverse("cases:create"), {"case_type": "online", "title": "หลอกลงทุน", "reported_at": "2026-11-16T09:00"})
    c = Case.objects.get(title="หลอกลงทุน")
    assert r.status_code == 302 and c.owner == investigator
    assert AuditLog.objects.filter(action="create", object_id=str(c.pk)).exists()


@pytest.mark.django_db
def test_seed_cases_covers_types_and_statuses():
    from django.core.management import call_command

    call_command("seed_demo")
    call_command("seed_cases")
    assert Case.objects.count() == 30
    assert set(Case.objects.values_list("case_type", flat=True)) == {"criminal", "online", "traffic"}
    assert set(Case.objects.values_list("status", flat=True)) == {"imported", "received", "investigating", "closed"}
