import pytest
from django.urls import reverse

from accounts.services import completeness


@pytest.mark.django_db
def test_profile_completeness_and_menu_dot(client_inv, investigator):
    c = completeness(investigator)
    assert c.missing_count == 5 and c.percent == 0
    assert "dot-alert" in client_inv.get("/").content.decode()
    url = reverse("accounts:profile")
    client_inv.post(
        url,
        {
            "section": "personal",
            "rank": "พ.ต.ท.",
            "first_name": "ทดสอบ",
            "last_name": "หนึ่ง",
            "position": "สารวัตร",
            "phone": "00 0000 0000",
        },
    )
    client_inv.post(url, {"section": "unit", "name": "กก.1", "address": "เชียงใหม่", "doc_prefix": "ตช 0000/"})
    for prefix, rows in (
        ("cmd", {"rank": "พ.ต.อ.", "name": "ผู้กำกับ", "position": "ผกก."}),
        ("team", {"rank": "ด.ต.", "name": "ชุดจับกุม"}),
        ("court", {"kind": "court", "name": "ศาลจังหวัด"}),
    ):
        data = {
            "section": {"cmd": "commanders", "team": "arrest_team", "court": "courts"}[prefix],
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        data.update({f"{prefix}-0-{k}": v for k, v in rows.items()})
        client_inv.post(url, data)
    investigator.refresh_from_db()
    c = completeness(investigator)
    assert c.missing_fields["courts"] == ["สำนักงานอัยการ"] and c.missing_count == 1
    client_inv.post(
        url,
        {
            "section": "courts",
            "court-TOTAL_FORMS": "2",
            "court-INITIAL_FORMS": "1",
            "court-MIN_NUM_FORMS": "0",
            "court-MAX_NUM_FORMS": "1000",
            "court-0-id": investigator.courts.first().pk,
            "court-0-kind": "court",
            "court-0-name": "ศาลจังหวัด",
            "court-1-kind": "prosecutor",
            "court-1-name": "อัยการจังหวัด",
        },
    )
    assert completeness(investigator).percent == 100
    assert "dot-alert" not in client_inv.get("/").content.decode()
