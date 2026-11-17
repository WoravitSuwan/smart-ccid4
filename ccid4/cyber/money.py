"""เส้นทางเงินและบัญชีม้า (B3): ธุรกรรม · ชั้นของบัญชี · ยอดเสียหาย → บัญชีทรัพย์ · นำเข้า statement · หนังสือถึงธนาคารแบบชุด."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.services import log
from core import thai

from . import identifiers
from .models import BankAccount, MoneyTransfer

ZERO = Decimal("0")


# ---------------------------------------------------------------- accounts and transfers
def get_account(case, user, number: str, bank: str = "", name: str = "", is_victim: bool = False) -> BankAccount:
    norm = identifiers.normalize("bank_account", number)
    if not norm:
        raise ValidationError(f"เลขบัญชี “{number}” ต้องมีตัวเลข 6–20 หลัก")
    acc, created = BankAccount.objects.get_or_create(
        case=case, norm=norm,
        defaults={"account_no": number.strip(), "bank": bank.strip(), "account_name": name.strip(), "is_victim": is_victim, "created_by": user},
    )  # fmt: skip
    changed = []
    for attr, value in (("bank", bank.strip()), ("account_name", name.strip())):
        if value and not getattr(acc, attr):
            setattr(acc, attr, value)
            changed.append(attr)
    if is_victim and not acc.is_victim:
        acc.is_victim = True
        changed.append("is_victim")
    if changed:
        acc.save(update_fields=changed + ["updated_at"])
    return acc


def parse_amount(value) -> Decimal:
    if isinstance(value, int | float | Decimal):
        return abs(Decimal(str(value)))
    t = re.sub(r"[^\d.\-]", "", thai.to_arabic_digits(str(value or "")))
    try:
        return abs(Decimal(t)) if t not in ("", "-", ".") else ZERO
    except InvalidOperation:
        return ZERO


@transaction.atomic
def add_transfer(case, user, *, to_no, amount, occurred_at=None, from_no="", from_bank="", from_name="", from_victim=True, to_bank="",
                 to_name="", channel="", reference="", evidence=None, source=MoneyTransfer.Source.MANUAL, note="") -> MoneyTransfer:  # fmt: skip
    amount = parse_amount(amount)
    if amount <= 0:
        raise ValidationError("จำนวนเงินต้องมากกว่า 0")
    to_acc = get_account(case, user, to_no, to_bank, to_name)
    from_acc = get_account(case, user, from_no, from_bank, from_name, is_victim=from_victim) if from_no else None
    if from_acc and from_acc.pk == to_acc.pk:
        raise ValidationError("บัญชีต้นทางและปลายทางต้องไม่ใช่บัญชีเดียวกัน")
    t = MoneyTransfer(case=case, occurred_at=occurred_at, from_account=from_acc, to_account=to_acc, amount=amount, channel=channel[:60],
                      reference=reference[:80], source=source, note=note[:300], created_by=user)  # fmt: skip
    if evidence:
        t.evidence.save(Path(evidence.name).name, evidence, save=False)
    t.save()
    log(user, "create", "money_transfer", t.pk, f"เพิ่มธุรกรรม {amount:,.2f} บาท ไปบัญชี {to_acc.masked} คดี {case}")
    sync_property(case)
    return t


@transaction.atomic
def delete_transfer(t: MoneyTransfer, user) -> None:
    case = t.case
    log(user, "delete", "money_transfer", t.pk, f"ลบธุรกรรม {t.amount:,.2f} บาท ไปบัญชี {t.to_account.masked} คดี {case}")
    t.evidence.delete(save=False) if t.evidence else None
    t.delete()
    for acc in case.bank_accounts.filter(
        incoming__isnull=True, outgoing__isnull=True, status=BankAccount.Status.NOT_REQUESTED, owner_info=""
    ):
        acc.delete()  # an account that no longer appears anywhere and was never worked on
    sync_property(case)


def from_victim(t: MoneyTransfer) -> bool:
    """Money that left the victim: from a victim account, or from an unknown source straight into the first layer."""
    return t.from_account_id is None or t.from_account.is_victim


def total_loss(case) -> Decimal:
    return sum((t.amount for t in case.transfers.select_related("from_account") if from_victim(t)), ZERO)


def sync_property(case) -> None:
    """B3: ยอดรวมที่เสียหายผูกกับบัญชีทรัพย์ของผู้กล่าวหาอัตโนมัติ (one row, kept in step)."""
    from parties.models import PropertyItem

    transfers = [t for t in case.transfers.select_related("from_account") if from_victim(t)]
    total = sum((t.amount for t in transfers), ZERO)
    row = PropertyItem.objects.filter(case=case, auto_source="money").first()
    if not total:
        if row:
            row.delete()
        return
    name = f"เงินที่โอนให้คนร้าย {len(transfers)} รายการ (ตามเส้นทางเงิน)"
    if row:
        if row.value != total or row.name != name:
            row.value, row.name = total, name
            row.save(update_fields=["value", "name"])
    else:
        PropertyItem.objects.create(case=case, name=name, quantity="1", value=total, auto_source="money",
                                    note="ระบบคำนวณจากหน้าเส้นทางเงิน แก้ที่หน้านั้น")  # fmt: skip


# ---------------------------------------------------------------- layers, summary and the flow diagram
def layers(case) -> dict[int, int | None]:
    """0 = victim account, 1 = first account the money reached, 2 = next … · None = not connected to the victim."""
    accounts = list(case.bank_accounts.all())
    nxt, first = defaultdict(set), set()
    for t in case.transfers.all():
        if t.from_account_id is None:
            first.add(t.to_account_id)
        else:
            nxt[t.from_account_id].add(t.to_account_id)
    layer = {a.pk: 0 for a in accounts if a.is_victim}
    frontier = set(layer)
    level = 0
    while True:
        level += 1
        reached = {b for a in frontier for b in nxt[a]} | (first if level == 1 else set())
        reached -= set(layer)
        if not reached:
            break
        for pk in reached:
            layer[pk] = level
        frontier = reached
    return {a.pk: layer.get(a.pk) for a in accounts}


@dataclass
class AccountRow:
    account: BankAccount
    layer: int | None
    received: Decimal = ZERO
    sent: Decimal = ZERO
    count_in: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None
    other_cases: list = field(default_factory=list)


def account_rows(case, link_map=None) -> list[AccountRow]:
    from .index import others_for

    lay = layers(case)
    rows = {a.pk: AccountRow(a, lay.get(a.pk)) for a in case.bank_accounts.select_related("party__person")}
    for t in case.transfers.all():
        r = rows[t.to_account_id]
        r.received += t.amount
        r.count_in += 1
        if t.occurred_at:
            r.first_at = min(r.first_at or t.occurred_at, t.occurred_at)
            r.last_at = max(r.last_at or t.occurred_at, t.occurred_at)
        if t.from_account_id in rows:
            rows[t.from_account_id].sent += t.amount
    if link_map is not None:
        for r in rows.values():
            r.other_cases = others_for(link_map, "bank_account", r.account.account_no)
    return sorted(rows.values(), key=lambda r: (r.layer is None, r.layer if r.layer is not None else 99, -r.received))


def flow_graph(case, link_map=None) -> dict:
    """Cytoscape elements: ผู้เสียหาย → บัญชีชั้นที่ 1 → ชั้นที่ 2 … edges summed per pair of accounts."""
    rows = account_rows(case, link_map)
    accuser = case.accuser
    nodes = [
        {
            "data": {
                "id": "victim",
                "label": f"ผู้เสียหาย\n{accuser.full_name}" if accuser else "ผู้เสียหาย",
                "kind": "victim",
                "layer": -1,
            }
        }
    ]
    for r in rows:
        a = r.account
        nodes.append({"data": {
            "id": f"a{a.pk}", "kind": "victim_account" if a.is_victim else "account", "layer": r.layer if r.layer is not None else 99,
            "label": f"{a.bank or 'ไม่ระบุธนาคาร'}\n{a.masked}\nรับ {r.received:,.0f} บาท", "status": a.status,
            "status_label": a.get_status_display(), "others": len(r.other_cases), "name": a.account_name,
        }})  # fmt: skip
    pairs: dict[tuple[str, str], list] = defaultdict(lambda: [ZERO, 0])
    victim_accounts = {r.account.pk for r in rows if r.account.is_victim}
    for pk in victim_accounts:
        pairs[("victim", f"a{pk}")]  # the victim owns these accounts (no amount on that edge)
    tx = defaultdict(list)
    for t in case.transfers.select_related("from_account", "to_account"):
        src = f"a{t.from_account_id}" if t.from_account_id else "victim"
        dst = f"a{t.to_account_id}"
        pairs[(src, dst)][0] += t.amount
        pairs[(src, dst)][1] += 1
        item = {"at": thai.format_datetime(timezone.localtime(t.occurred_at)) if t.occurred_at else "-",
                "from": t.from_account.masked if t.from_account else "ผู้เสียหาย", "to": t.to_account.masked,
                "amount": f"{t.amount:,.2f}", "channel": t.channel, "ref": t.reference}  # fmt: skip
        tx[dst].append(item)
        tx[src].append(item)
    edges = [
        {
            "data": {
                "id": f"e{i}",
                "source": s,
                "target": d,
                "amount": float(v[0]),
                "label": f"{v[0]:,.0f} บาท ({v[1]} ครั้ง)" if v[1] else "เป็นเจ้าของ",
            }
        }  # fmt: skip
        for i, ((s, d), v) in enumerate(pairs.items())
    ]
    return {"elements": nodes + edges, "transactions": tx}


# ---------------------------------------------------------------- import a bank statement (Excel/CSV) with column mapping
TARGETS = [
    ("date", "วันที่", True), ("time", "เวลา", False), ("from_no", "จากบัญชี", False), ("from_bank", "ธนาคารต้นทาง", False),
    ("to_no", "ไปบัญชี (ปลายทาง)", True), ("to_bank", "ธนาคารปลายทาง", False), ("to_name", "ชื่อบัญชีปลายทาง", False),
    ("amount", "จำนวนเงิน", True), ("channel", "ช่องทาง", False), ("reference", "เลขอ้างอิง", False),
]  # fmt: skip
HINTS = {
    "date": r"วันที่|date|วันทำรายการ",
    "time": r"เวลา|time",
    "from_no": r"จากบัญชี|บัญชีต้นทาง|from.?acc|บัญชีผู้โอน",
    "from_bank": r"ธนาคารต้นทาง|ธนาคารผู้โอน|from.?bank",
    "to_no": r"ไปบัญชี|บัญชีปลายทาง|บัญชีผู้รับ|to.?acc|โอนไป|เลขที่บัญชีผู้รับ|counterparty",
    "to_bank": r"ธนาคารปลายทาง|ธนาคารผู้รับ|to.?bank|ธนาคาร$|^bank",
    "to_name": r"ชื่อบัญชี|ชื่อผู้รับ|name",
    "amount": r"จำนวนเงิน|ยอดเงิน|ยอดโอน|amount|ถอน|debit|withdraw",
    "channel": r"ช่องทาง|channel|รายการ|description",
    "reference": r"อ้างอิง|ref",
}


def read_table(name: str, data: bytes) -> tuple[list[str], list[list[str]]]:
    if name.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        rows = [
            ["" if v is None else (v.isoformat(sep=" ") if isinstance(v, datetime) else str(v)) for v in r]
            for r in ws.iter_rows(values_only=True)
        ]
    else:
        for enc in ("utf-8-sig", "cp874"):  # Thai bank exports are often Windows-874
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValidationError("อ่านไฟล์ไม่ได้ (ต้องเป็น CSV แบบ UTF-8 หรือ Windows-874 หรือ Excel .xlsx)")
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|") if text.strip() else csv.excel
        rows = [r for r in csv.reader(io.StringIO(text), dialect)]
    rows = [[c.strip() for c in r] for r in rows if any(str(c).strip() for c in r)]
    if len(rows) < 2:
        raise ValidationError("ไฟล์ต้องมีหัวตาราง 1 แถว และรายการอย่างน้อย 1 แถว")
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return rows[0], rows[1:]


def guess_mapping(headers: list[str]) -> dict[str, int | None]:
    used, out = set(), {}
    for key, _, _ in TARGETS:
        idx = next((i for i, h in enumerate(headers) if i not in used and re.search(HINTS[key], h, re.I)), None)
        out[key] = idx
        if idx is not None:
            used.add(idx)
    return out


def parse_when(date_text: str, time_text: str = "") -> datetime | None:
    t = thai.to_arabic_digits(f"{date_text} {time_text}".strip())
    if not t:
        return None
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", t)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.match(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})(?:\s+(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?)?", t)
        if not m:
            m2 = re.match(
                r"(\d{1,2})\s*(" + "|".join(thai.MONTHS_SHORT + thai.MONTHS) + r")\s*(\d{2,4})(?:\s+(\d{1,2})[:.](\d{2}))?", t
            )
            if not m2:
                raise ValueError(f"อ่านวันที่ไม่ได้: {date_text}")
            months = thai.MONTHS_SHORT + thai.MONTHS
            d, mo, y = int(m2[1]), months.index(m2[2]) % 12 + 1, int(m2[3])
            hh, mm, ss = int(m2[4] or 0), int(m2[5] or 0), 0
            return _aware(y, mo, d, hh, mm, ss)
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
    hh, mm, ss = int(m[4] or 0), int(m[5] or 0), int(m[6] or 0)
    return _aware(y, mo, d, hh, mm, ss)


def _aware(y, mo, d, hh, mm, ss) -> datetime:
    if y < 100:
        y += 2500 if y >= 40 else 2000  # 2-digit year on Thai statements is BE ("69" = 2569)
    if y > 2400:
        y -= 543
    return timezone.make_aware(datetime.combine(datetime(y, mo, d).date(), time(hh, mm, ss)))


@dataclass
class ImportResult:
    created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@transaction.atomic
def import_rows(case, user, rows, mapping: dict, fixed_from: dict | None = None, evidence_name: str = "") -> ImportResult:
    """Create transfers from mapped rows; money coming in (no destination or amount 0) is skipped; duplicates are skipped."""
    res = ImportResult()
    col = lambda r, k: (r[mapping[k]] if mapping.get(k) is not None and mapping[k] < len(r) else "")  # noqa: E731
    for n, r in enumerate(rows, start=2):
        to_no, amount = col(r, "to_no"), parse_amount(col(r, "amount"))
        if not identifiers.normalize("bank_account", to_no) or amount <= 0:
            res.skipped += 1
            continue
        try:
            when = parse_when(col(r, "date"), col(r, "time"))
        except ValueError as e:
            res.errors.append(f"แถว {n}: {e}")
            continue
        from_no = col(r, "from_no") or (fixed_from or {}).get("no", "")
        dup = MoneyTransfer.objects.filter(case=case, to_account__norm=identifiers.normalize("bank_account", to_no), amount=amount,
                                           occurred_at=when, reference=col(r, "reference")[:80]).exists()  # fmt: skip
        if dup:
            res.skipped += 1
            continue
        try:
            with transaction.atomic():
                add_transfer(case, user, to_no=to_no, amount=amount, occurred_at=when, from_no=from_no,
                             from_bank=col(r, "from_bank") or (fixed_from or {}).get("bank", ""), from_name=(fixed_from or {}).get("name", ""),
                             from_victim=(fixed_from or {}).get("victim", True), to_bank=col(r, "to_bank"), to_name=col(r, "to_name"),
                             channel=col(r, "channel"), reference=col(r, "reference"), source=MoneyTransfer.Source.IMPORT,
                             note=f"นำเข้าจาก {evidence_name}"[:300] if evidence_name else "")  # fmt: skip
            res.created += 1
        except ValidationError as e:
            res.errors.append(f"แถว {n}: {' '.join(e.messages)}")
    log(
        user,
        "import",
        "money_transfer",
        case.pk,
        f"นำเข้าธุรกรรม {res.created} รายการ (ข้าม {res.skipped}, ผิดพลาด {len(res.errors)}) คดี {case}",
    )
    return res


# ---------------------------------------------------------------- batch letters to banks (one per bank)
LIBRARY = Path(__file__).resolve().parent / "library"
PURPOSES = {
    "info": ("ขอข้อมูลบัญชีเงินฝาก", "ขอความอนุเคราะห์ข้อมูลเจ้าของบัญชี (ชื่อ ที่อยู่ เลขประจำตัวประชาชน หมายเลขโทรศัพท์ที่ผูกกับบัญชี) "
             "ภาพถ่ายผู้เปิดบัญชี รายการเดินบัญชี{ช่วง} และข้อมูลการเข้าใช้งานบริการทางอินเทอร์เน็ต (IP address อุปกรณ์) ของบัญชีตามตารางด้านล่าง "
             "ตามอำนาจ ป.วิ.อาญา มาตรา ๑๓๒"),
    "freeze": ("ขอให้ระงับการทำธุรกรรมและขอข้อมูลบัญชี", "ขอให้ระงับการทำธุรกรรมของบัญชีตามตารางด้านล่างไว้เป็นการชั่วคราว ตามกฎหมายว่าด้วยมาตรการป้องกันและปราบปราม"
               "อาชญากรรมทางเทคโนโลยี และขอข้อมูลเจ้าของบัญชีพร้อมรายการเดินบัญชี{ช่วง} เพื่อประกอบการสอบสวน"),
}  # fmt: skip
LETTER_BLOCKS = [
    ("l", "ที่ {หนังสือ.เลขที่}"), ("r", "{หน่วย.ชื่อ}"), ("r", "{หน่วย.ที่อยู่}"), ("r", "{หนังสือ.วันที่}"),
    ("l", "เรื่อง {หนังสือ.เรื่อง}"), ("l", "เรียน {หนังสือ.เรียน}"),
    ("l", "สิ่งที่ส่งมาด้วย รายการบัญชีตามตารางในหนังสือนี้ จำนวน {หนังสือ.จำนวนบัญชี} บัญชี"),
    ("p", "ด้วยพนักงานสอบสวน{หน่วย.ชื่อ} กำลังสอบสวนคดีอาญาที่ {คดี.เลขคดี} ความผิดฐาน {คดี.ฐานความผิด} "
          "จากการสอบสวนพบว่าผู้เสียหายได้โอนเงินเข้าบัญชีของธนาคารท่านตามรายการด้านล่าง จึง{หนังสือ.คำขอ}"),
    ("table", ("ลำดับ", "เลขบัญชี", "ชื่อบัญชี", "ยอดที่รับจากเส้นทางเงิน (บาท)"), ("{บัญชี.ลำดับ}", "{บัญชี.เลข}", "{บัญชี.ชื่อ}", "{บัญชี.ยอด}")),
    ("p", "ข้อมูลที่ได้รับจะใช้เพื่อการสอบสวนเท่านั้น จึงเรียนมาเพื่อโปรดพิจารณาให้ความร่วมมือ และขอขอบคุณมา ณ โอกาสนี้"),
    ("sign", "พนักงานสอบสวน", "{พงส.ยศชื่อ}", "{พงส.ตำแหน่ง}"),
    ("l", "ผู้ประสานงาน {พงส.ยศชื่อ} โทร {พงส.โทรศัพท์}"),
]  # fmt: skip


def letter_template() -> bytes:
    path = LIBRARY / "bank_batch_letter.docx"
    if not path.exists():
        from documents.management.commands.build_doc_templates import build

        LIBRARY.mkdir(exist_ok=True)
        doc = build("bank_batch_letter", LETTER_BLOCKS)
        doc.core_properties.author, doc.core_properties.title = "CCID4", "bank_batch_letter"
        doc.save(path)
    return path.read_bytes()


@transaction.atomic
def bank_letters(case, user, account_ids, purpose: str, date_from=None, date_to=None) -> tuple[bytes, list]:
    """One letter per bank listing the chosen accounts → ZIP (.docx, and PDF when LibreOffice is installed) + OutgoingLetter rows."""
    from cases.models import OutgoingLetter
    from core.convert import docx_to_pdf, soffice_available
    from documents import engine
    from documents.context import build, d

    if purpose not in PURPOSES:
        raise ValidationError("เลือกประเภทหนังสือ")
    rows = {r.account.pk: r for r in account_rows(case)}
    accounts = [rows[pk].account for pk in account_ids if pk in rows and not rows[pk].account.is_victim]
    if not accounts:
        raise ValidationError("เลือกบัญชีปลายทางอย่างน้อย 1 บัญชี")
    missing = [a.masked for a in accounts if not a.bank]
    if missing:
        raise ValidationError(f"ระบุธนาคารของบัญชีก่อน: {', '.join(missing)}")
    by_bank = defaultdict(list)
    for a in accounts:
        by_bank[a.bank].append(a)
    period = f" ตั้งแต่วันที่ {d(date_from)} ถึงวันที่ {d(date_to)}" if date_from and date_to else ""
    subject, ask = PURPOSES[purpose]
    template = letter_template()
    buf, letters = io.BytesIO(), []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for bank, accs in sorted(by_bank.items()):
            values = build(case, user=user) | {
                "หนังสือ": {"เลขที่": "", "วันที่": d(timezone.localdate()), "เรื่อง": subject, "เรียน": f"ผู้จัดการธนาคาร{bank}",
                            "จำนวนบัญชี": str(len(accs)), "คำขอ": ask.replace("{ช่วง}", period)},
                "บัญชี": [{"ลำดับ": str(i), "เลข": a.account_no, "ชื่อ": a.account_name or "-", "ยอด": f"{rows[a.pk].received:,.2f}"}
                          for i, a in enumerate(accs, start=1)],
            }  # fmt: skip
            data, _ = engine.fill(template, values, thai_digits=False, allow_empty={"หนังสือ.เลขที่"})
            safe = "".join(ch if unicodedata.category(ch)[0] in "LMN" else "-" for ch in bank).strip("-")[:60]
            z.writestr(f"{subject}-{safe}.docx", data)
            if soffice_available():
                pdf = docx_to_pdf(data)
                if pdf:
                    z.writestr(f"{subject}-{safe}.pdf", pdf)
            letters.append(OutgoingLetter.objects.create(
                case=case, template="bank", subject=subject, recipient=f"ผู้จัดการธนาคาร{bank}", created_by=user,
                fields={"bank": bank, "accounts": [a.account_no for a in accs], "freeze": purpose == "freeze", "batch": True},
            ))  # fmt: skip
    if purpose == "info":
        BankAccount.objects.filter(pk__in=[a.pk for a in accounts], status=BankAccount.Status.NOT_REQUESTED).update(
            status=BankAccount.Status.REQUESTED
        )
    log(user, "export", "bank_letters", case.pk, f"ออกหนังสือ{subject} {len(letters)} ธนาคาร {len(accounts)} บัญชี คดี {case}")
    return buf.getvalue(), letters
