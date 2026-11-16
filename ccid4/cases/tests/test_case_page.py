"""Phase 2: the case page (A4.1–A4.5, manual entry)."""

import pytest
from django.urls import reverse

from audit.models import AuditLog
from cases.tests.factories import CaseFactory, PartyFactory, PersonFactory
from parties.models import CaseParty, Person, Statement

REAL_ID = "3000000000456"


def page_data(response) -> dict:
    """Turn the forms rendered on the case page into POST data, exactly as a browser would send them unchanged."""
    ctx = response.context
    data = {"tab": "info"}
    forms = [ctx["case_form"], ctx["accuser_form"]]
    for key in ("property_fs", "suspect_fs", "witness_fs"):
        fs = ctx[key]
        mgmt = fs.management_form
        for name in ("TOTAL_FORMS", "INITIAL_FORMS", "MIN_NUM_FORMS", "MAX_NUM_FORMS"):
            data[f"{fs.prefix}-{name}"] = mgmt[name].value()
        forms += list(fs.forms)
    for form in forms:
        for bf in form:
            value = bf.value()
            if value is None or value is False:
                continue
            if value is True:
                data[bf.html_name] = "on"
                continue
            value = bf.field.widget.format_value(value)
            if isinstance(value, list):
                value = value[0] if value else ""
            if value is None:
                continue
            data[bf.html_name] = value
    return data


def add_person(data, prefix, **fields):
    n = int(data[f"{prefix}-TOTAL_FORMS"])
    for k, v in fields.items():
        data[f"{prefix}-{n}-{k}"] = v
    data[f"{prefix}-TOTAL_FORMS"] = str(n + 1)
    return n


@pytest.fixture
def case(investigator):
    c = CaseFactory(owner=investigator)
    PartyFactory(case=c, role="accuser", person=PersonFactory(first_name="ผู้แจ้ง", citizen_id=REAL_ID))
    return c


def url(case):
    return reverse("cases:detail", args=[case.pk])


@pytest.mark.django_db
def test_page_has_three_parts_and_masks_id(client_inv, case):
    r = client_inv.get(url(case))
    html = r.content.decode()
    for text in ("ข้อมูลคดี", "ผู้กล่าวหา", "ผู้ต้องหา", "พยาน", "งานและกำหนดเวลา", "ออกเอกสาร", "Timeline คดี", "ยังไม่บันทึก"):
        assert text in html
    assert REAL_ID not in html
    assert "3-xxxx-xxxxx-45-6" in html
    assert AuditLog.objects.filter(action="view", object_type="case", object_id=str(case.pk)).exists()


@pytest.mark.django_db
def test_unchanged_save_changes_nothing(client_inv, case):
    data = page_data(client_inv.get(url(case)))
    before = AuditLog.objects.filter(action__in=["edit", "create"]).count()
    r = client_inv.post(url(case), data, follow=True)
    assert "ไม่มีข้อมูลที่เปลี่ยน" in r.content.decode()
    assert AuditLog.objects.filter(action__in=["edit", "create"]).count() == before
    assert Person.objects.get(first_name="ผู้แจ้ง").citizen_id == REAL_ID  # masked value did not overwrite the real one


@pytest.mark.django_db
def test_save_all_tabs_in_one_go(client_inv, case):
    data = page_data(client_inv.get(url(case)))
    data["c-offense"] = "ฉ้อโกงประชาชน และนำเข้าข้อมูลเท็จ"
    data["c-max_penalty_months"] = "60"
    data["acc-statement"] = "ข้าพเจ้าถูกหลอกให้โอนเงิน"
    add_person(data, "prop", name="โทรศัพท์มือถือ", quantity="1", value="12000", status="not_returned")
    add_person(data, "sus", first_name="ผู้ต้องหาหนึ่ง", citizen_id="1100000000017", arrived_at="2026-11-10T08:30")
    add_person(data, "sus", first_name="ผู้ต้องหาสอง", statement="ปฏิเสธตลอดข้อกล่าวหา")
    add_person(data, "wit", first_name="พยานหนึ่ง")
    r = client_inv.post(url(case), data)
    assert r.status_code == 302 and r["Location"].endswith("#info")
    case.refresh_from_db()
    assert case.offense.startswith("ฉ้อโกงประชาชน และ")
    suspects = list(case.parties.filter(role="suspect").order_by("order"))
    assert [s.person.first_name for s in suspects] == ["ผู้ต้องหาหนึ่ง", "ผู้ต้องหาสอง"]
    assert [s.order for s in suspects] == [1, 2]
    assert suspects[0].person.citizen_id == "1100000000017" and suspects[0].arrived_at is not None
    assert suspects[1].current_statement.text == "ปฏิเสธตลอดข้อกล่าวหา"
    assert case.parties.filter(role="witness").count() == 1
    assert case.properties.get().name == "โทรศัพท์มือถือ"
    assert case.accuser.first_name == "ผู้แจ้ง"
    assert AuditLog.objects.filter(action="create", object_type="party").count() == 3


@pytest.mark.django_db
def test_invalid_tab_saves_nothing(client_inv, case):
    data = page_data(client_inv.get(url(case)))
    data["c-offense"] = "ควรไม่ถูกบันทึก"
    add_person(data, "sus", first_name="คนผิด", citizen_id="12345")
    r = client_inv.post(url(case), data)
    assert r.status_code == 400
    assert r.context["errors"]["suspects"] and r.context["first_error_tab"] == "suspects"
    case.refresh_from_db()
    assert case.offense != "ควรไม่ถูกบันทึก"
    assert not CaseParty.objects.filter(role="suspect").exists()


@pytest.mark.django_db
def test_person_without_name_is_rejected_blank_is_ignored(client_inv, case):
    data = page_data(client_inv.get(url(case)))
    add_person(data, "wit", phone="0800000000")
    assert client_inv.post(url(case), data).status_code == 400
    data = page_data(client_inv.get(url(case)))
    add_person(data, "wit")  # added then left empty
    assert client_inv.post(url(case), data).status_code == 302
    assert not case.parties.filter(role="witness").exists()


@pytest.mark.django_db
def test_statement_versions(client_inv, case, investigator):
    acc = case.parties.get(role="accuser")
    for text in ("ฉบับแรก", "ฉบับแรก", "ฉบับแก้ไข"):
        data = page_data(client_inv.get(url(case)))
        data["acc-statement"] = text
        client_inv.post(url(case), data)
    versions = list(Statement.objects.filter(party=acc).order_by("version").values_list("version", "text"))
    assert versions == [(1, "ฉบับแรก"), (2, "ฉบับแก้ไข")]
    assert Statement.objects.filter(party=acc, created_by=investigator).count() == 2
    html = client_inv.get(url(case)).content.decode()
    assert "ดูฉบับก่อนหน้า" in html


@pytest.mark.django_db
def test_delete_suspect(client_inv, case):
    sus = PartyFactory(case=case, role="suspect", person=PersonFactory(first_name="จะลบ"))
    data = page_data(client_inv.get(url(case)))
    data["sus-0-DELETE"] = "on"
    assert client_inv.post(url(case), data).status_code == 302
    assert not CaseParty.objects.filter(pk=sus.pk).exists()
    assert not Person.objects.filter(first_name="จะลบ").exists()
    assert AuditLog.objects.filter(action="delete", object_type="party").exists()


@pytest.mark.django_db
def test_cannot_touch_party_of_other_case(client_inv, case, investigator):
    other = PartyFactory(case=CaseFactory(owner=investigator), role="suspect", person=PersonFactory(first_name="คดีอื่น"))
    data = page_data(client_inv.get(url(case)))
    n = add_person(data, "sus", first_name="แอบแก้")
    data[f"sus-{n}-party_id"] = str(other.pk)
    client_inv.post(url(case), data)
    other.refresh_from_db()
    assert other.person.first_name == "คดีอื่น" and other.case_id != case.pk


@pytest.mark.django_db
def test_reveal_id_is_permission_checked_and_audited(client_inv, client, case, make_user):
    acc = case.parties.get(role="accuser")
    reveal = reverse("cases:reveal_id", args=[case.pk, acc.pk])
    r = client_inv.post(reveal, {"field": "acc-citizen_id"})
    assert r.status_code == 200 and REAL_ID in r.content.decode() and 'name="acc-citizen_id"' in r.content.decode()
    log = AuditLog.objects.filter(action="reveal", object_type="person").get()
    assert log.object_id == str(acc.person_id)
    commander = make_user(role="commander")
    client.force_login(commander)
    assert client.post(reveal, {"field": "acc-citizen_id"}).status_code == 403
    assert AuditLog.objects.filter(action="reveal").count() == 1


@pytest.mark.django_db
def test_commander_is_read_only(client, case, make_user):
    client.force_login(make_user(role="commander"))
    r = client.get(url(case))
    assert r.status_code == 200 and "อ่านอย่างเดียว" in r.content.decode()
    assert r.context["case_form"].fields["offense"].disabled
    assert client.post(url(case), {"c-offense": "x"}).status_code == 403
