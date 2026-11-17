"""Phase 9: เส้นทางเงิน/บัญชีม้า (B3), เครือข่ายคดี (B4), กระเป๋าคริปโต (B5). All data is fictional."""

import hashlib
import io
import json
import socket
import zipfile
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from docx import Document

from audit.models import AuditLog
from cases.models import OutgoingLetter
from cases.tests.factories import CaseFactory, PartyFactory, PersonFactory
from core.models import Notification
from cyber import crypto, identifiers, index, money, services
from cyber.models import BankAccount, CryptoTx, Identifier, MergeProposal, MoneyTransfer
from parties.models import PropertyItem

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"


def b58check_encode(body: bytes) -> str:
    raw = body + hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4]
    n, s = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = crypto.B58[r] + s
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + s


TRON = b58check_encode(bytes([0x41]) + bytes(range(20)))
EVM = "0x" + "ab" * 20
BTC_LEGACY = b58check_encode(bytes([0x00]) + bytes(range(1, 21)))
BTC_BECH32 = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"  # BIP-173 test vector


@pytest.fixture
def two_cases(make_user):
    a_owner, b_owner = make_user(role="investigator", username="inv_a"), make_user(role="investigator", username="inv_b")
    a = CaseFactory(owner=a_owner, case_no="101/2569")
    b = CaseFactory(owner=b_owner, case_no="202/2569")
    return a, b


# ---------------------------------------------------------------- normalize, mask, crypto formats
def test_identifiers_are_normalized_before_matching():
    assert (
        identifiers.normalize("bank_account", "123-4-56789-0")
        == identifiers.normalize("bank_account", "1234567890")
        == "1234567890"
    )
    assert identifiers.normalize("bank_account", "๑๒๓-๔-๕๖๗๘๙-๐") == "1234567890"
    assert identifiers.normalize("phone", "+66 81-234-5678") == identifiers.normalize("phone", "081 234 5678") == "0812345678"
    assert identifiers.normalize("social", "@Scam.Shop") == identifiers.normalize("social", "LINE: scam.shop") == "scam.shop"
    assert identifiers.normalize("url", "https://www.Shop-Scam.example/promo/") == "shop-scam.example/promo"
    assert identifiers.normalize("domain", "http://shop-scam.example/x") == "shop-scam.example"
    assert identifiers.normalize("bank_account", "12-3") == ""  # too short to link cases safely
    assert identifiers.mask("bank_account", "1234567890") == "xxx-x-x6789-x"
    assert identifiers.mask("phone", "0812345678") == "081-xxx-5678"
    assert identifiers.digest("bank_account", "1234567890") != identifiers.digest("phone", "1234567890")


def test_crypto_address_formats_and_checksums_are_checked_locally():
    assert crypto.validate("btc", BTC_LEGACY)[0] and crypto.validate("btc", BTC_BECH32)[0]
    assert not crypto.validate("btc", BTC_LEGACY[:-1] + ("2" if BTC_LEGACY[-1] != "2" else "3"))[0]  # checksum broken
    assert not crypto.validate("btc", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5")[0]
    assert crypto.validate("tron", TRON)[0] and not crypto.validate("tron", "T" + "1" * 33)[0]
    assert crypto.validate("evm", EVM)[0] and not crypto.validate("evm", "0x1234")[0]
    assert crypto.detect(EVM) == "evm" and crypto.detect(TRON) == "tron" and crypto.detect(BTC_BECH32) == "btc"


# ---------------------------------------------------------------- acceptance 7: บัญชีซ้ำข้ามคดี
@pytest.mark.django_db
def test_same_account_in_two_cases_shows_found_in_other_case_on_both(two_cases, client):
    a, b = two_cases
    money.add_transfer(a, a.owner, to_no="123-4-56789-0", to_bank="กสิกรไทย", amount="5000")
    money.add_transfer(b, b.owner, to_no="1234567890", to_bank="กสิกรไทย", amount="8000")
    assert set(Identifier.objects.filter(kind="bank_account").values_list("digest", flat=True)).__len__() == 1
    for case in (a, b):
        client.force_login(case.owner)
        html = client.get(reverse("cyber:case", args=[case.pk])).content.decode()
        assert "พบในคดีอื่น 1 คดี" in html
        html = client.get(reverse("cases:detail", args=[case.pk])).content.decode()
        assert "เชื่อมโยงกับคดีอื่น 1 คดี" in html and "พบในคดีอื่น 1 บัญชี" in html
    # both owners were told once; saving again does not repeat it
    assert Notification.objects.filter(user=a.owner, kind="identifier_match").count() == 1
    assert Notification.objects.filter(user=b.owner, kind="identifier_match").count() == 1
    money.add_transfer(b, b.owner, to_no="1234567890", amount="100")
    assert Notification.objects.filter(kind="identifier_match").count() == 2
    assert not Identifier.objects.filter(masked__contains="56789")  # the index never holds the full number


@pytest.mark.django_db
def test_phone_of_a_person_in_two_cases_is_badged(two_cases, client):
    a, b = two_cases
    PartyFactory(case=a, role="suspect", person=PersonFactory(phone="081-234-5678", citizen_id="1100000000011"))
    PartyFactory(case=b, role="suspect", person=PersonFactory(phone="+66812345678", citizen_id="1100000000029"))
    client.force_login(a.owner)
    html = client.get(reverse("cases:detail", args=[a.pk])).content.decode()
    assert "เบอร์โทรนี้พบในคดีอื่น 1 คดี" in html and "202/2569" in html


@pytest.mark.django_db
def test_other_cases_show_only_number_and_owner(two_cases, client):
    a, b = two_cases
    PartyFactory(
        case=b, role="suspect", person=PersonFactory(first_name="ลับเฉพาะ", phone="0899999999", citizen_id="1100000000037")
    )
    PartyFactory(case=a, role="witness", person=PersonFactory(phone="0899999999", citizen_id="1100000000045"))
    client.force_login(a.owner)
    data = client.get(reverse("cyber:network_data")).json()
    other = next(n["data"] for n in data["nodes"] if n["data"]["id"] == f"c-{b.pk}")
    assert other["visible"] is False and other["url"] == "" and other["owner"] == b.owner.display_name
    assert "ลับเฉพาะ" not in json.dumps(data, ensure_ascii=False)  # where it sits in case B is not shown
    assert client.get(reverse("cyber:case", args=[b.pk])).status_code == 404
    assert client.get(reverse("cases:detail", args=[b.pk])).status_code == 404
    r = client.get(reverse("cyber:network_export"))
    from openpyxl import load_workbook

    rows = [list(r) for r in load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True)]
    assert any(row[3] == b.label and "ไม่มีสิทธิ์" in row[5] for row in rows if row[3])
    assert AuditLog.objects.filter(action="export", object_type="network").exists()
    html = client.get(reverse("cyber:network") + f"?case={a.pk}").content.decode()
    assert "เครือข่ายคดี" in html and "data-net" in html


# ---------------------------------------------------------------- money flow
@pytest.mark.django_db
def test_layers_total_loss_and_property_row(investigator):
    case = CaseFactory(owner=investigator)
    PartyFactory(case=case, role="accuser")
    money.add_transfer(
        case, investigator, from_no="111-1-11111-1", from_bank="กรุงเทพ", to_no="222-2-22222-2", to_bank="กสิกรไทย", amount="10000"
    )
    money.add_transfer(
        case, investigator, to_no="333-3-33333-3", to_bank="ไทยพาณิชย์", amount="2,500.50"
    )  # from unknown victim account
    money.add_transfer(
        case, investigator, from_no="2222222222", from_victim=False, to_no="444-4-44444-4", to_bank="กรุงไทย", amount="9000"
    )
    acc = {a.norm: a.pk for a in case.bank_accounts.all()}
    lay = money.layers(case)
    assert (
        lay[acc["1111111111"]] == 0
        and lay[acc["2222222222"]] == 1
        and lay[acc["3333333333"]] == 1
        and lay[acc["4444444444"]] == 2
    )
    assert money.total_loss(case) == Decimal("12500.50")  # money passed on by a mule is not counted twice
    row = PropertyItem.objects.get(case=case, auto_source="money")
    assert row.value == Decimal("12500.50") and "2 รายการ" in row.name
    graph = money.flow_graph(case)
    ids = {e["data"]["id"] for e in graph["elements"]}
    assert "victim" in ids and any(e["data"].get("source") == f"a{acc['2222222222']}" for e in graph["elements"])
    for t in list(case.transfers.all()):
        money.delete_transfer(t, investigator)
    assert not PropertyItem.objects.filter(case=case, auto_source="money").exists() and not case.bank_accounts.exists()


@pytest.mark.django_db
def test_transfer_validation(investigator):
    case = CaseFactory(owner=investigator)
    with pytest.raises(ValidationError, match="มากกว่า 0"):
        money.add_transfer(case, investigator, to_no="2222222222", amount="0")
    with pytest.raises(ValidationError, match="6–20"):
        money.add_transfer(case, investigator, to_no="12-3", amount="10")
    with pytest.raises(ValidationError, match="ไม่ใช่บัญชีเดียวกัน"):
        money.add_transfer(case, investigator, from_no="2222222222", to_no="222-2-22222-2", amount="10")


STATEMENT = (
    "วันที่,เวลา,รายการ,บัญชีปลายทาง,ธนาคารปลายทาง,ชื่อบัญชีปลายทาง,ถอน,ฝาก,เลขอ้างอิง\n"
    '03/03/2569,10:15,โอนเงินพร้อมเพย์,123-4-56789-0,กสิกรไทย,นายสมมติ ทดลอง,"5,000.00",,REF0001\n'
    '03/03/2569,10:40,โอนเงิน,987-6-54321-0,ไทยพาณิชย์,นางสาวตัวอย่าง ทดสอบ,"12,500.00",,REF0002\n'
    '04/03/2569,09:00,รับโอน,,,,,"1,000.00",REF0003\n'
)


@pytest.mark.django_db
def test_statement_import_with_column_mapping(client_inv, investigator):
    case = CaseFactory(owner=investigator)
    up = io.BytesIO(STATEMENT.encode("cp874"))
    up.name = "statement.csv"
    r = client_inv.post(reverse("cyber:import", args=[case.pk]), {"file": up})
    assert r["Location"] == reverse("cyber:import_map", args=[case.pk])
    html = client_inv.get(r["Location"]).content.decode()
    assert "จับคู่คอลัมน์" in html and "REF0001" in html
    headers, _ = money.read_table("statement.csv", STATEMENT.encode("cp874"))
    guess = money.guess_mapping(headers)
    assert headers[guess["to_no"]] == "บัญชีปลายทาง" and headers[guess["amount"]] == "ถอน" and headers[guess["date"]] == "วันที่"
    post = {f"map-{k}": "" if v is None else str(v) for k, v in guess.items()}
    post |= {"fixed_no": "111-1-11111-1", "fixed_bank": "กรุงเทพ", "fixed_victim": "on"}
    client_inv.post(reverse("cyber:import_map", args=[case.pk]), post)
    ts = list(case.transfers.select_related("from_account", "to_account"))
    assert len(ts) == 2 and sum(t.amount for t in ts) == Decimal("17500")
    assert ts[0].occurred_at.year == 2026 and ts[0].occurred_at.month == 3 and ts[0].from_account.is_victim
    assert {t.source for t in ts} == {MoneyTransfer.Source.IMPORT}
    res = money.import_rows(case, investigator, money.read_table("s.csv", STATEMENT.encode())[1], guess, {"no": "1111111111"})
    assert res.created == 0 and res.skipped == 3  # duplicates and the deposit are skipped


def test_dates_on_thai_statements():
    assert money.parse_when("03/03/2569", "10:15").strftime("%Y-%m-%d %H:%M") == "2026-03-03 10:15"
    assert money.parse_when("2026-03-03 08:05:09").strftime("%H:%M:%S") == "08:05:09"
    assert money.parse_when("3 มี.ค. 69 14:20").strftime("%Y-%m-%d %H:%M") == "2026-03-03 14:20"
    assert money.parse_amount("-1,234.50") == Decimal("1234.50")


@pytest.mark.django_db
def test_batch_letters_one_per_bank(investigator):
    case = CaseFactory(owner=investigator)
    for no, bank in (("123-4-56789-0", "กสิกรไทย"), ("123-4-00000-1", "กสิกรไทย"), ("987-6-54321-0", "ไทยพาณิชย์")):
        money.add_transfer(case, investigator, to_no=no, to_bank=bank, to_name="บัญชีสมมติ", amount="1000")
    ids = list(case.bank_accounts.values_list("pk", flat=True))
    data, letters = money.bank_letters(case, investigator, ids, "info")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        docs = [n for n in z.namelist() if n.endswith(".docx")]
        assert len(docs) == 2
        kbank = next(n for n in docs if "กสิกรไทย" in n)
        text = "".join(t.text or "" for t in Document(io.BytesIO(z.read(kbank))).element.body.iter(W))
    assert "123-4-56789-0" in text and "123-4-00000-1" in text and "987-6-54321-0" not in text and "{" not in text
    assert "ผู้จัดการธนาคารกสิกรไทย" in text and "ป.วิ.อาญา" in text
    assert OutgoingLetter.objects.filter(case=case, template="bank").count() == 2 == len(letters)
    assert set(case.bank_accounts.values_list("status", flat=True)) == {BankAccount.Status.REQUESTED}
    assert AuditLog.objects.filter(action="export", object_type="bank_letters").exists()
    money.add_transfer(case, investigator, to_no="5555555555", amount="10")
    with pytest.raises(ValidationError, match="ระบุธนาคาร"):
        money.bank_letters(case, investigator, [case.bank_accounts.get(norm="5555555555").pk], "freeze")


@pytest.mark.django_db
def test_slip_is_read_into_a_draft_and_saved_only_after_review(client_inv, investigator):
    case = CaseFactory(owner=investigator)
    slip = io.BytesIO(
        "โอนเงินสำเร็จ 12 ต.ค. 69 14:05 น.\nนางสาวทดลอง ใจดี ธ.กรุงเทพ xxx-x-x1234-x\nนายสมมติ ทดลอง ธ.กสิกรไทย 123-4-56789-0\n"
        "จำนวน: 8,500.00 บาท\nเลขที่รายการ: 016285140512ABC".encode()
    )
    slip.name = "slip.txt"
    from django.core.files.uploadedfile import SimpleUploadedFile

    up = SimpleUploadedFile("slip.txt", slip.getvalue(), content_type="text/plain")
    html = client_inv.post(reverse("cyber:slip", args=[case.pk]), {"slip": up}).content.decode()
    assert "ร่างโดย AI – รอตรวจ" in html and 'value="123-4-56789-0"' in html and 'value="8500' in html
    assert not case.transfers.exists()  # nothing is saved by the AI
    token = html.split('name="slip_token" value="')[1].split('"')[0]
    client_inv.post(reverse("cyber:transfer_add", args=[case.pk]),
                    {"to_no": "123-4-56789-0", "to_bank": "กสิกรไทย", "amount": "8500", "occurred_at": "2026-10-12T14:05",
                     "from_no": "", "from_victim": "on", "slip_token": token})  # fmt: skip
    t = case.transfers.get()
    assert t.source == MoneyTransfer.Source.SLIP and t.evidence and t.amount == Decimal("8500")


# ---------------------------------------------------------------- crypto (B5)
@pytest.mark.django_db
def test_wallets_transactions_and_cryptotrace_export_without_network(investigator, monkeypatch):
    real_connect = socket.socket.connect

    def guarded(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and not (host.startswith(("127.", "::1", "localhost", "/")) or host == ""):
            raise AssertionError(f"network call to {host}")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    case = CaseFactory(owner=investigator)
    suspect = PartyFactory(case=case, role="suspect", person=PersonFactory(citizen_id="1100000000053"))
    services.add_wallet(case, investigator, "tron", TRON, "กระเป๋าที่ผู้เสียหายโอนไป", party=suspect)
    with pytest.raises(ValidationError, match="TRON"):
        services.add_wallet(case, investigator, "tron", "T" + "x" * 33)
    services.add_tx(case, investigator, "evm", "0x" + "1" * 64, "1500.5", "USDT", EVM, "0x" + "cd" * 20)
    csv_data = f"tx_hash,network,from,to,amount,asset,time\n{'a' * 64},tron,{TRON},{TRON},250,USDT,2026-03-03 10:00\nbad,tron,,,1,USDT,\n"
    n, errors = services.import_tx_csv(case, investigator, csv_data.encode())
    assert n == 1 and len(errors) == 1 and CryptoTx.objects.filter(case=case).count() == 2
    data = services.crypto_export(case, investigator)
    assert data["schema"] == "ccid4.cryptotrace/1" and data["wallets"][0]["role"] == "suspect" and data["wallets"][0]["valid"]
    assert {t["amount"] for t in data["transactions"]} == {"1500.5", "250"}
    assert Identifier.objects.filter(case=case, kind="crypto").count() >= 2


@pytest.mark.django_db
def test_wallet_in_two_cases_links_them(two_cases):
    a, b = two_cases
    services.add_wallet(a, a.owner, "evm", EVM)
    services.add_wallet(b, b.owner, "evm", EVM.upper().replace("0X", "0x"))  # hex is case-insensitive
    assert len(index.links(a)) == 1


# ---------------------------------------------------------------- merge proposals and the bell
@pytest.mark.django_db
def test_merge_proposal_goes_to_the_head(two_cases, make_user, client):
    a, b = two_cases
    head = make_user(role="supervisor", username="head")
    client.force_login(a.owner)
    client.post(reverse("cyber:merge_propose", args=[a.pk]), {"other": b.pk, "reason": "ใช้บัญชีม้าชุดเดียวกันและคนร้ายกลุ่มเดียวกัน"})
    p = MergeProposal.objects.get()
    assert Notification.objects.filter(user=head, kind="merge_proposal").exists()
    assert client.post(reverse("cyber:merge_decide", args=[p.pk]), {"decision": "approve"}).status_code == 403
    client.force_login(head)
    assert "ใช้บัญชีม้าชุดเดียวกัน" in client.get(reverse("cyber:merge_list")).content.decode()
    client.post(reverse("cyber:merge_decide", args=[p.pk]), {"decision": "approve", "note": "รวมได้"})
    p.refresh_from_db()
    assert p.status == "approved" and p.decided_by == head
    assert Notification.objects.filter(user=a.owner, kind="merge_decision").exists()


@pytest.mark.django_db
def test_bell_lists_opens_and_clears(client_inv, investigator):
    from core.notify import notify

    notify([investigator], "test", "มีงานใหม่", "รายละเอียด", reverse("cases:list"))
    html = client_inv.get(reverse("cases:list")).content.decode()
    assert 'class="bell-count">1<' in html
    assert "มีงานใหม่" in client_inv.get(reverse("core:notifications")).content.decode()
    n = Notification.objects.get()
    assert client_inv.get(reverse("core:notification_open", args=[n.pk]))["Location"] == reverse("cases:list")
    n.refresh_from_db()
    assert n.read_at
    notify([investigator], "test", "อีกเรื่อง", url="https://evil.example/")
    m = Notification.objects.get(title="อีกเรื่อง")
    assert client_inv.get(reverse("core:notification_open", args=[m.pk]))["Location"] == reverse("core:home")
    client_inv.post(reverse("core:notifications_read_all"))
    assert not Notification.objects.filter(read_at__isnull=True).exists()


@pytest.mark.django_db
def test_money_page_and_read_only_view(investigator, client_inv, make_user, client):
    case = CaseFactory(owner=investigator)
    money.add_transfer(case, investigator, to_no="123-4-56789-0", to_bank="กสิกรไทย", amount="5000")
    html = client_inv.get(reverse("cyber:case", args=[case.pk])).content.decode()
    for text in ("เส้นทางเงินและบัญชีม้า", "บัญชีปลายทาง (บัญชีม้า)", "แผนภาพเส้นทางเงิน", "xxx-x-x6789-x", "อ่านจากสลิป", "ส่งออก JSON"):
        assert text in html
    assert "123-4-56789-0" not in html  # masked by default
    acc = case.bank_accounts.get()
    r = client_inv.post(reverse("cyber:account_reveal", args=[case.pk, acc.pk]))
    assert "123-4-56789-0" in r.content.decode() and AuditLog.objects.filter(action="reveal", object_type="bank_account").exists()
    flow = client_inv.get(reverse("cyber:flow", args=[case.pk])).json()
    assert any(e["data"]["id"] == "victim" for e in flow["elements"])
    commander = make_user(role="commander")
    client.force_login(commander)
    html = client.get(reverse("cyber:case", args=[case.pk])).content.decode()
    assert "บันทึกธุรกรรม" not in html
    assert client.post(reverse("cyber:transfer_add", args=[case.pk]), {"to_no": "2222222222", "amount": "1"}).status_code == 403
    html = client_inv.get(reverse("cases:detail", args=[case.pk])).content.decode()
    assert "เปิดหน้าเส้นทางเงิน" in html and 'data-tab="money"' in html
