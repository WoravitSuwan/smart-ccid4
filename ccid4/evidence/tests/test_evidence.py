"""Phase 10 (B6): พยานหลักฐานดิจิทัลและของกลาง. Fictional files only."""

import hashlib
import io
import os
import stat

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from docx import Document
from PIL import Image

from audit.models import AuditLog
from cases.tests.factories import CaseFactory
from core.models import Notification
from documents import catalog, context, engine
from evidence import services
from evidence.models import CustodyEvent, EvidenceFile, Exhibit

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def case(investigator):
    return CaseFactory(owner=investigator)


@pytest.mark.django_db
def test_upload_is_hashed_locked_and_write_once(case, investigator):
    data = png_bytes()
    ev = services.receive(case, investigator, SimpleUploadedFile("แชท.png", data, "image/png"), source="ผู้เสียหายส่งทางไลน์")
    assert ev.sha256 == hashlib.sha256(data).hexdigest() and ev.kind == "image" and ev.size == len(data)
    assert not os.stat(ev.file.path).st_mode & stat.S_IWUSR  # read-only on disk
    ev.sha256 = "0" * 64
    with pytest.raises(ValidationError, match="แก้ไขไม่ได้"):
        ev.save()
    with pytest.raises(ValidationError, match="ถอนออกจากสำนวน"):
        EvidenceFile.objects.get(pk=ev.pk).delete()
    assert CustodyEvent.objects.filter(evidence=ev, action="receive").exists()


@pytest.mark.django_db
def test_integrity_check_finds_tampering(case, investigator, make_user):
    head = make_user(role="supervisor")
    ev = services.receive(case, investigator, SimpleUploadedFile("หลักฐาน.pdf", b"%PDF-1.4 fictional", "application/pdf"))
    assert services.verify(ev, investigator)
    os.chmod(ev.file.path, 0o644)
    with open(ev.file.path, "ab") as f:
        f.write(b"changed")
    assert not services.verify(ev, investigator)
    assert CustodyEvent.objects.filter(evidence=ev, action="verify", ok=False).exists()
    assert Notification.objects.filter(user=head, kind="evidence_integrity").exists()


@pytest.mark.django_db
def test_preview_download_and_chain_of_custody(case, investigator, client_inv):
    ev = services.receive(case, investigator, SimpleUploadedFile("ภาพ.png", png_bytes(), "image/png"))
    html = client_inv.get(reverse("evidence:file", args=[case.pk, ev.pk])).content.decode()
    assert reverse("evidence:view", args=[case.pk, ev.pk]) in html and ev.sha256 in html
    r = client_inv.get(reverse("evidence:view", args=[case.pk, ev.pk]))
    assert (
        r.status_code == 200 and r["X-Frame-Options"] == "SAMEORIGIN" and "frame-ancestors 'self'" in r["Content-Security-Policy"]
    )
    client_inv.get(reverse("evidence:view", args=[case.pk, ev.pk]), HTTP_RANGE="bytes=10-20")  # a seek is not a new opening
    assert CustodyEvent.objects.filter(evidence=ev, action="view").count() == 1
    r = client_inv.post(reverse("evidence:download", args=[case.pk, ev.pk]), {"reason": ""})
    assert r.status_code == 302 and not CustodyEvent.objects.filter(action="download").exists()
    r = client_inv.post(reverse("evidence:download", args=[case.pk, ev.pk]), {"reason": "ส่งตรวจพิสูจน์ที่ พฐ."})
    assert r.status_code == 200 and "attachment" in r["Content-Disposition"]
    e = CustodyEvent.objects.get(action="download")
    assert e.by == investigator and e.reason == "ส่งตรวจพิสูจน์ที่ พฐ."
    assert AuditLog.objects.filter(action="download", object_type="evidence").exists()
    html = client_inv.get(reverse("evidence:case", args=[case.pk])).content.decode()
    assert "บันทึกการครอบครอง" in html and "ส่งตรวจพิสูจน์ที่ พฐ." in html


@pytest.mark.django_db
def test_upload_view_and_other_users(case, client_inv, client, make_user):
    r = client_inv.post(reverse("evidence:upload", args=[case.pk]),
                        {"files": [SimpleUploadedFile("a.png", png_bytes(), "image/png"), SimpleUploadedFile("b.txt", b"log", "text/plain")],
                         "title": "ภาพหน้าจอ", "source": "โทรศัพท์ผู้เสียหาย", "is_digital": "on"})  # fmt: skip
    assert r.status_code == 302 and case.evidence_files.count() == 2
    assert set(case.evidence_files.values_list("title", flat=True)) == {"ภาพหน้าจอ (1)", "ภาพหน้าจอ (2)"}
    other = make_user(role="investigator")
    client.force_login(other)
    assert client.get(reverse("evidence:case", args=[case.pk])).status_code == 404
    commander = make_user(role="commander")
    client.force_login(commander)
    assert client.get(reverse("evidence:case", args=[case.pk])).status_code == 200
    assert (
        client.post(reverse("evidence:upload", args=[case.pk]), {"files": [SimpleUploadedFile("c.png", png_bytes())]}).status_code
        == 403
    )


@pytest.mark.django_db
def test_exhibit_register_handover_and_documents(case, investigator):
    phone = services.add_exhibit(case, investigator, name="โทรศัพท์มือถือ (สมมติ)", quantity="1", unit="เครื่อง", condition="ใช้งานได้",
                                 source="ตรวจยึดจากผู้ต้องหา", storage="ห้องเก็บของกลาง", is_digital=True)  # fmt: skip
    services.add_exhibit(case, investigator, name="สมุดบัญชีเงินฝาก (สมมติ)", quantity="2", unit="เล่ม")
    dump = services.receive(
        case, investigator, SimpleUploadedFile("dump.bin", b"fictional extraction", "application/octet-stream"), exhibit=phone
    )
    with pytest.raises(ValidationError, match="เหตุผล"):
        services.update_exhibit(phone, investigator, {"storage": "ตู้ 2"}, "")
    services.update_exhibit(phone, investigator, {"storage": "ตู้ 2"}, "ย้ายตามระเบียบ")
    services.handover(phone, investigator, "พฐ.จว.สมมติ", "ส่งตรวจพิสูจน์ข้อมูล", Exhibit.Status.LAB)
    phone.refresh_from_db()
    assert phone.status == "lab" and phone.holder == "พฐ.จว.สมมติ"
    services.take_back(phone, investigator, "พฐ.จว.สมมติ", "ตรวจเสร็จแล้ว")
    phone.refresh_from_db()
    assert phone.status == "held" and not phone.holder
    actions = list(CustodyEvent.objects.filter(exhibit=phone).values_list("action", flat=True))
    assert {"receive", "move", "handover", "return"} <= set(actions)
    values = context.build(case)
    assert [r["ชื่อ"] for r in values["ของกลาง"]] == ["โทรศัพท์มือถือ (สมมติ)", "สมุดบัญชีเงินฝาก (สมมติ)"]
    assert dump.sha256 in values["หลักฐานดิจิทัล"][0]["แฮช"]
    for key, expect in (("exhibits", "สมุดบัญชีเงินฝาก (สมมติ)"), ("digital_seizure", dump.sha256)):
        doc = catalog.get(key)
        data, missing = engine.fill(doc.template_bytes(), catalog.apply_aliases(doc, values), thai_digits=doc.digits == "thai")
        text = "".join(t.text or "" for t in Document(io.BytesIO(data)).element.body.iter(W))
        assert expect in text and "{" not in text


@pytest.mark.django_db
def test_withdraw_needs_a_reason_and_keeps_the_file(case, investigator):
    ev = services.receive(case, investigator, SimpleUploadedFile("x.png", png_bytes(), "image/png"))
    with pytest.raises(ValidationError):
        services.withdraw(ev, investigator, "สั้น")
    services.withdraw(ev, investigator, "ซ้ำกับไฟล์ลำดับที่ 1 ที่รับเข้าก่อนหน้า")
    ev.refresh_from_db()
    assert ev.withdrawn_at and os.path.exists(ev.file.path)
    assert context.digital_rows(case) == []
