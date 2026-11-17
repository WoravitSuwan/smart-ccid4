"""บัญชีออนไลน์ · กระเป๋าและธุรกรรมคริปโต (B5) · อ่านสลิป (AI, ต้องตรวจทาน) · คำขอรวมสำนวน (B4)."""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from audit.services import log
from core.notify import notify

from . import crypto
from .models import CryptoTx, CryptoWallet, MergeProposal, OnlineAccount

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "cryptotrace_export.schema.json"
SCHEMA_ID = "ccid4.cryptotrace/1"


# ---------------------------------------------------------------- online accounts (LINE ID, Facebook, URL …)
def add_online(case, user, kind: str, value: str, display_name: str = "", party=None, note: str = "") -> OnlineAccount:
    from .identifiers import normalize

    if kind not in OnlineAccount.Kind.values:
        raise ValidationError("เลือกประเภทบัญชี")
    ident = {"url": "url", "email": "email"}.get(kind, "social")
    if not normalize(ident, value):
        raise ValidationError("ข้อมูลสั้นเกินไปหรือรูปแบบไม่ถูกต้อง")
    o = OnlineAccount.objects.create(case=case, kind=kind, value=value.strip()[:300], display_name=display_name[:200], party=party,
                                     note=note[:300], created_by=user)  # fmt: skip
    log(user, "create", "online_account", o.pk, f"เพิ่ม{o.get_kind_display()}ในคดี {case}")
    return o


# ---------------------------------------------------------------- crypto (B5) — local checks only, no blockchain explorer
def add_wallet(case, user, network: str, address: str, label: str = "", party=None, note: str = "") -> CryptoWallet:
    address = (address or "").strip()
    ok, msg = crypto.validate(network, address)
    if not ok:
        raise ValidationError(msg)
    if CryptoWallet.objects.filter(case=case, network=network, address=address).exists():
        raise ValidationError("มีกระเป๋านี้ในคดีแล้ว")
    w = CryptoWallet.objects.create(
        case=case, network=network, address=address, label=label[:120], party=party, note=note[:300], created_by=user
    )
    log(user, "create", "crypto_wallet", w.pk, f"เพิ่มกระเป๋า {w.get_network_display()} ในคดี {case}")
    return w


def parse_crypto_amount(value) -> Decimal:
    try:
        v = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError("จำนวนไม่ถูกต้อง") from None
    if v <= 0:
        raise ValidationError("จำนวนต้องมากกว่า 0")
    return v


def add_tx(
    case, user, network, tx_hash, amount, asset="USDT", from_address="", to_address="", occurred_at=None, note="", source="manual"
) -> CryptoTx:
    tx_hash = (tx_hash or "").strip()
    if network not in CryptoWallet.Network.values:
        raise ValidationError("เลือกเครือข่าย")
    if not crypto.tx_hash_ok(network, tx_hash):
        raise ValidationError("Tx hash ต้องเป็นเลขฐานสิบหก 64 ตัว (EVM ขึ้นต้นด้วย 0x)")
    for addr in (from_address, to_address):
        if addr and not crypto.validate(network, addr.strip())[0]:
            raise ValidationError(f"ที่อยู่ {addr} ไม่ตรงรูปแบบของเครือข่าย {network.upper()}")
    t, created = CryptoTx.objects.get_or_create(
        case=case, network=network, tx_hash=tx_hash, to_address=(to_address or "").strip(),
        defaults={"from_address": (from_address or "").strip(), "amount": parse_crypto_amount(amount), "asset": (asset or "").upper()[:20],
                  "occurred_at": occurred_at, "note": note[:300], "source": source, "created_by": user},
    )  # fmt: skip
    if created:
        log(user, "create", "crypto_tx", t.pk, f"เพิ่มธุรกรรมคริปโต {t.amount} {t.asset} ในคดี {case}")
    return t


CSV_ALIASES = {
    "tx_hash": ("tx_hash", "txhash", "hash", "transaction hash", "txid"),
    "network": ("network", "chain", "เครือข่าย"),
    "from": ("from", "from_address", "sender", "จาก"),
    "to": ("to", "to_address", "receiver", "ไป"),
    "amount": ("amount", "value", "quantity", "จำนวน"),
    "asset": ("asset", "token", "symbol", "สินทรัพย์"),
    "time": ("time", "timestamp", "datetime", "date", "วันเวลา"),
}


def import_tx_csv(case, user, data: bytes, default_network: str = "") -> tuple[int, list[str]]:
    from .money import parse_when

    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    cols = {
        k: next((h for h in (reader.fieldnames or []) if h and h.strip().lower() in names), None)
        for k, names in CSV_ALIASES.items()
    }
    if not cols["tx_hash"] or not cols["amount"]:
        raise ValidationError("ไฟล์ CSV ต้องมีคอลัมน์ tx_hash และ amount")
    created, errors = 0, []
    for n, row in enumerate(reader, start=2):
        get = lambda k, row=row: (row.get(cols[k]) or "").strip() if cols[k] else ""  # noqa: E731
        network = (get("network") or default_network).lower()
        network = {"eth": "evm", "ethereum": "evm", "bsc": "evm", "polygon": "evm", "bitcoin": "btc", "trx": "tron"}.get(
            network, network
        )
        try:
            when = parse_when(get("time")) if get("time") else None
            before = CryptoTx.objects.filter(case=case).count()
            with transaction.atomic():
                add_tx(
                    case,
                    user,
                    network,
                    get("tx_hash"),
                    get("amount"),
                    get("asset") or "USDT",
                    get("from"),
                    get("to"),
                    when,
                    source="import",
                )
            created += CryptoTx.objects.filter(case=case).count() - before
        except (ValidationError, ValueError) as e:
            errors.append(f"แถว {n}: {' '.join(getattr(e, 'messages', [str(e)]))}")
    log(user, "import", "crypto_tx", case.pk, f"นำเข้าธุรกรรมคริปโต {created} รายการ คดี {case}")
    return created, errors


def crypto_export(case, user) -> dict:
    """JSON for a future CryptoTrace system (schema in cyber/schemas/cryptotrace_export.schema.json)."""
    from ai import schema

    roles = {"accuser": "victim", "suspect": "suspect", "witness": "witness"}
    data = {
        "schema": SCHEMA_ID,
        "exported_at": timezone.now().isoformat(timespec="seconds"),
        "case": {"ref": case.label, "offense": case.offense or "", "unit": case.owner.unit_group or "", "reported_at": case.reported_at.date().isoformat()},
        "wallets": [
            {"network": w.network, "address": w.address, "label": w.label, "role": roles.get(w.party.role, "unknown") if w.party else "unknown",
             "valid": crypto.validate(w.network, w.address)[0]}
            for w in case.crypto_wallets.select_related("party")
        ],
        "transactions": [
            {"network": t.network, "tx_hash": t.tx_hash, "from": t.from_address or None, "to": t.to_address or None,
             "amount": format(t.amount.normalize(), "f"), "asset": t.asset, "time": t.occurred_at.isoformat() if t.occurred_at else None}
            for t in case.crypto_txs.all()
        ],
    }  # fmt: skip
    problems = schema.errors(data, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    if problems:
        raise ValidationError("ข้อมูลไม่ตรง schema: " + "; ".join(problems[:5]))
    log(user, "export", "crypto", case.pk, f"ส่งออกข้อมูลคริปโต (JSON {SCHEMA_ID}) คดี {case}")
    return data


# ---------------------------------------------------------------- slip → draft transfer (AI in the unit, the officer reviews before saving)
def read_slip(case, user, data: bytes, content_type: str) -> dict:
    from ai import llm, ocr

    text = ocr.read(data, content_type)
    out = llm.complete_json("extract_slip", text, user=user, case=case)
    log(user, "ai", "money_transfer", case.pk, f"อ่านสลิปด้วย AI ในเครื่อง คดี {case} (รอตรวจทาน)")
    return out


# ---------------------------------------------------------------- merge proposals (B4)
def propose_merge(case, other, user, reason: str) -> MergeProposal:
    from accounts.models import User

    if case.pk == other.pk:
        raise ValidationError("เลือกคดีอื่น")
    if len((reason or "").strip()) < 10:
        raise ValidationError("ระบุเหตุผลอย่างน้อย 10 ตัวอักษร")
    if MergeProposal.objects.filter(case=case, other=other, status=MergeProposal.Status.PENDING).exists():
        raise ValidationError("มีคำขอรวมสำนวนคู่นี้รอพิจารณาอยู่แล้ว")
    p = MergeProposal.objects.create(case=case, other=other, reason=reason.strip(), proposed_by=user)
    heads = User.objects.filter(role="supervisor", is_active=True)
    groups = {g for g in (case.owner.unit_group, other.owner.unit_group) if g}
    if groups:
        heads = heads.filter(unit_group__in=groups) or User.objects.filter(role="supervisor", is_active=True)
    notify(heads, "merge_proposal", f"เสนอรวมสำนวน {case.label} กับ {other.label}", f"โดย {user.display_name}: {reason[:200]}",
           reverse("cyber:merge_list"))  # fmt: skip
    log(user, "create", "merge_proposal", p.pk, f"เสนอรวมสำนวน {case} กับ {other}")
    return p


def decide_merge(p: MergeProposal, user, approve: bool, note: str = "") -> MergeProposal:
    if p.status != MergeProposal.Status.PENDING:
        raise ValidationError("คำขอนี้พิจารณาแล้ว")
    p.status = MergeProposal.Status.APPROVED if approve else MergeProposal.Status.REJECTED
    p.decided_by, p.decided_at, p.decision_note = user, timezone.now(), note[:500]
    p.save()
    notify([p.proposed_by, p.case.owner, p.other.owner], "merge_decision", f"คำขอรวมสำนวน {p.case.label} กับ {p.other.label}: {p.get_status_display()}",
           note[:300], reverse("cases:detail", args=[p.case.pk]))  # fmt: skip
    log(user, "edit", "merge_proposal", p.pk, f"พิจารณาคำขอรวมสำนวน {p.case} กับ {p.other}: {p.get_status_display()}")
    return p
