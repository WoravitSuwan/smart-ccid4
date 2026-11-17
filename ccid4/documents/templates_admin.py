"""เทมเพลตเอกสารแบบจัดการเอง (B11): อัปโหลด .docx → สแกน {placeholder} → จับคู่กับข้อมูลในระบบ → เปิดใช้ (มีเวอร์ชัน ย้อนกลับได้)."""

from __future__ import annotations

import hashlib
import re

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max

from audit.services import log

from . import catalog, context, engine
from .models import CustomDocType, TemplateVersion


def flatten(values: dict, prefix: str = "") -> dict[str, str]:
    """{path: kind} for every value a template can use: text, or a table row field (list item)."""
    out = {}
    for k, v in values.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            out |= flatten(v, f"{path}.")
        elif isinstance(v, list):
            fields = set()
            for row in v:
                if isinstance(row, dict):
                    fields |= set(row)
            out[path] = "list"
            for f in sorted(fields):
                out[f"{path}.{f}"] = "row"
        else:
            out[path] = "text"
    return out


FALLBACK_ROWS = {"ทรัพย์": ["ลำดับ", "ชื่อ", "จำนวน", "มูลค่า", "หมายเหตุ"], "ของกลาง": ["ลำดับ", "ชื่อ", "จำนวน", "สภาพ", "ที่มา", "ที่เก็บ", "สถานะ", "หมายเหตุ"],
                 "หลักฐานดิจิทัล": ["ลำดับ", "ชื่อ", "แฮช", "ที่มา"], "บันทึก": ["วันที่", "รายการ", "อ้างอิง"], "เอกสารในสำนวน": ["ชื่อ", "วันที่", "แผ่น"]}  # fmt: skip


def system_fields(case=None) -> dict[str, str]:
    """All system paths, read from the document context of a real case (values are not used, only the names)."""
    from cases.models import Case

    case = case or Case.objects.alive().order_by("-pk").first()
    if case is None:
        return {}
    first_party = case.parties.first()
    fields = flatten(context.build(case, party=first_party))
    for lst, cols in FALLBACK_ROWS.items():  # empty lists in this case still show their columns
        for c in cols:
            fields.setdefault(f"{lst}.{c}", "row")
    return dict(sorted(fields.items()))


def guess(placeholders: list[str], doc: catalog.DocType | None, known: dict[str, str]) -> dict[str, str]:
    aliases = dict(doc.fields) if doc else {}
    out = {}
    for p in placeholders:
        if p in known:
            out[p] = p
        elif p in aliases:
            out[p] = aliases[p]
        else:
            out[p] = ""
    return out


def docs() -> dict[str, catalog.DocType]:
    return catalog.load()[1]


@transaction.atomic
def upload(key: str, user, upload_file, note: str = "") -> TemplateVersion:
    doc = docs().get(key)
    if doc is None:
        raise ValidationError("ไม่พบแบบเอกสารนี้")
    data = upload_file.read()
    if not upload_file.name.lower().endswith(".docx") or not data.startswith(b"PK"):
        raise ValidationError("ต้องเป็นไฟล์ Word .docx")
    try:
        found = engine.find_placeholders(data)
    except Exception as e:  # noqa: BLE001 - a broken or non-Word file
        raise ValidationError("อ่านไฟล์ Word ไม่ได้") from e
    if not found:
        raise ValidationError("ไม่พบช่อง {placeholder} ในไฟล์ (พิมพ์ชื่อช่องในวงเล็บปีกกา เช่น {คดี.เลขคดี})")
    version = (TemplateVersion.objects.select_for_update().filter(key=key).aggregate(m=Max("version"))["m"] or 0) + 1
    tv = TemplateVersion(key=key, version=version, sha256=hashlib.sha256(data).hexdigest(), placeholders=found,
                         mapping=guess(found, doc, system_fields()), digits=doc.digits, note=note[:300], uploaded_by=user)  # fmt: skip
    safe = re.sub(r"[^A-Za-z0-9_-]", "", key)
    tv.file.save(f"{safe}-v{version}.docx", ContentFile(data), save=False)
    tv.save()
    log(user, "create", "template", tv.pk, f"อัปโหลดเทมเพลต {doc.title} เวอร์ชัน {version} ({len(found)} ช่อง)")
    return tv


@transaction.atomic
def save_mapping(tv: TemplateVersion, user, mapping: dict[str, str], digits: str, activate_now: bool) -> TemplateVersion:
    known = system_fields()
    bad = [f"{k} → {v}" for k, v in mapping.items() if v and v not in known]
    if bad:
        raise ValidationError("ข้อมูลในระบบที่เลือกไม่มีอยู่: " + ", ".join(bad))
    tv.mapping = {p: mapping.get(p, "") for p in tv.placeholders}
    tv.digits = digits if digits in ("thai", "arabic") else tv.digits
    tv.save(update_fields=["mapping", "digits"])
    log(user, "edit", "template", tv.pk, f"จับคู่ช่องของเทมเพลต {tv.key} v{tv.version}")
    if activate_now:
        activate(tv, user)
    return tv


@transaction.atomic
def activate(tv: TemplateVersion, user) -> None:
    TemplateVersion.objects.filter(key=tv.key, active=True).exclude(pk=tv.pk).update(active=False)
    tv.active = True
    tv.save(update_fields=["active"])
    log(user, "edit", "template", tv.pk, f"เปิดใช้เทมเพลต {tv.key} v{tv.version}")


def use_builtin(key: str, user) -> None:
    doc = docs().get(key)
    if doc is None or not doc.path.exists():
        raise ValidationError("เอกสารนี้ไม่มีไฟล์มาตรฐานของระบบ")
    TemplateVersion.objects.filter(key=key, active=True).update(active=False)
    log(user, "edit", "template", key, f"กลับไปใช้ไฟล์มาตรฐานของ {doc.title}")


def add_custom(user, title: str, group: str, per: str) -> CustomDocType:
    if len((title or "").strip()) < 3:
        raise ValidationError("ตั้งชื่อเอกสาร")
    groups, _ = catalog.load_builtin()
    c = CustomDocType.objects.create(key="tmp", title=title.strip()[:300], group=group if group in groups else groups[-1],
                                     per=per if per in dict(CustomDocType.PER_CHOICES) else "", created_by=user)  # fmt: skip
    c.key = f"unit_{c.pk}"
    c.save(update_fields=["key"])
    log(user, "create", "template", c.key, f"เพิ่มเอกสารใหม่ของหน่วย: {c.title}")
    return c


def try_fill(tv: TemplateVersion, case, user) -> tuple[bytes, list[str]]:
    """Fill this version with one case to check the mapping before (or after) using it."""
    doc = docs()[tv.key]
    party = case.parties.filter(role__in=catalog.PER_ROLES.get(doc.per or "", [])).first() if doc.per else None
    values = context.build(case, party=party)
    with tv.file.open("rb") as f:
        data, missing = engine.fill(f.read(), catalog.apply_aliases_for(tv, doc, values), thai_digits=tv.digits == "thai",
                                    allow_empty={k for k, v in tv.mapping.items() if not v})  # fmt: skip
    log(user, "export", "template", tv.pk, f"ทดลองเติมเทมเพลต {tv.key} v{tv.version} กับคดี {case}")
    return data, missing
