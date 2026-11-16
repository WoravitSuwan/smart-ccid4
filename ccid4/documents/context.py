"""Values for document placeholders. Keys are Thai so template authors can read them (`{คดี.เลขคดี}`).

Everything is turned into display text here (dates in the official long form, money with commas); the engine
only swaps digits to Thai when the template asks for it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from core import thai


def d(value) -> str:
    """Official date: ๕ กรกฎาคม ๒๕๖๙ (digits are converted by the engine)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = timezone.localtime(value)
    return thai.format_date(value, "long")


def t(value) -> str:
    if value is None:
        return ""
    return f"{timezone.localtime(value):%H.%M} นาฬิกา"


def money(value) -> str:
    if value in (None, ""):
        return ""
    return f"{Decimal(value):,.2f}"


def age(birth: date | None, on: date | None = None) -> str:
    if not birth:
        return ""
    on = on or timezone.localdate()
    years = on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))
    return str(years)


def person_values(person) -> dict:
    if person is None:
        return {}
    return {
        "คำนำหน้า": person.title,
        "ชื่อ": person.first_name,
        "นามสกุล": person.last_name,
        "ชื่อเต็ม": person.full_name,
        "อายุ": age(person.birth_date),
        "วันเกิด": d(person.birth_date),
        "เลขบัตร": person.citizen_id,
        "หนังสือเดินทาง": person.passport_no,
        "เพศ": person.gender,
        "สัญชาติ": person.nationality,
        "เชื้อชาติ": person.ethnicity or person.nationality,
        "ศาสนา": person.religion,
        "อาชีพ": person.occupation,
        "การศึกษา": person.education,
        "โทรศัพท์": person.phone,
        "ที่อยู่": person.address,
        "บ้านเลขที่": person.house_no,
        "หมู่": person.moo,
        "ตำบล": person.tambon,
        "อำเภอ": person.amphoe,
        "จังหวัด": person.province,
        "ผู้ติดต่อ": " ".join(x for x in (person.contact_name, person.contact_relation, person.contact_phone) if x),
        "ตำหนิ": person.marks,
    }


def party_values(cp) -> dict:
    if cp is None:
        return {}
    st = cp.current_statement
    out = person_values(cp.person) | {
        "บทบาท": cp.get_role_display(),
        "ลำดับ": cp.order,
        # AI drafts keep their label in the document until an investigator confirms them (CLAUDE.md)
        "คำให้การ": (("[ร่างโดย AI – รอตรวจ]\n" if st.needs_review else "") + st.text) if st else "",
        "วันจับ": d(cp.arrested_at),
        "เวลาจับ": t(cp.arrested_at),
        "วันถึง": d(cp.arrived_at),
        "เวลาถึง": t(cp.arrived_at),
        "สถานที่ควบคุม": cp.custody_place,
        "ประกัน": cp.bail_status,
        "แนวคำให้การ": cp.get_plea_display() if cp.plea else "",
        "วันแจ้งสิทธิ": d(cp.rights_informed_at),
        "เวลาแจ้งสิทธิ": t(cp.rights_informed_at),
    }
    if cp.role == "suspect":
        out["ฝากขัง"] = remand_values(cp)
    return out


def remand_values(cp) -> dict:
    """The next remand request for this suspect (คำร้องขอฝากขังครั้งแรก/ครั้งต่อไป)."""
    from deadlines import rules
    from deadlines.services import custody_for, load_rules, suggested_start

    rs = load_rules()
    c = custody_for(cp, cp.case, rs)
    if not c.plan:
        return {}
    p = rs.params[c.rule_key]
    n = c.plan.used_times + 1
    try:
        nxt = rules.next_period(c.plan, suggested_start(c), p)
    except ValueError:
        return {"ผัดที่": c.plan.used_times, "ครบแล้ว": "ใช้สิทธิ์ครบแล้ว"}
    previous = "".join(
        f"ผัดที่ {i} ตั้งแต่วันที่ {d(x.start_date)} ถึงวันที่ {d(x.end_date)}\n" for i, x in enumerate(c.periods, start=1)
    ).strip()
    return {
        "ผัดที่": n,
        "ครั้ง": "ครั้งแรก" if n == 1 else f"ครั้งที่ {n}",
        "ตั้งแต่": d(nxt.start),
        "ถึง": d(nxt.end),
        "จำนวนวัน": nxt.days,
        "ผัดก่อน": previous or "-",
        "รวมวันที่ฝากแล้ว": c.plan.used_days,
        "กติกา": c.plan.name,
        "ฐานกฎหมาย": c.plan.ref,
    }


def property_rows(items) -> list[dict]:
    return [
        {"ชื่อ": i.name, "จำนวน": i.quantity, "มูลค่า": money(i.value), "สถานะ": i.get_status_display(), "หมายเหตุ": i.note}
        for i in items
    ]


def build(case, party=None, letter=None, user=None) -> dict:
    """All values for one document. `party` / `letter` is the item of a per-person / per-letter document."""
    now = timezone.localtime()
    owner = case.owner
    unit = getattr(owner, "unit", None) if hasattr(owner, "unit") else None
    commander = owner.commanders.filter(is_default=True).first() or owner.commanders.first()
    courts = list(owner.courts.all())
    parties = list(case.parties.select_related("person").all())
    accuser = next((p for p in parties if p.role == "accuser"), None)
    suspects = [p for p in parties if p.role == "suspect"]
    witnesses = [p for p in parties if p.role == "witness"]
    props = list(case.properties.all())
    returned = [p for p in props if p.status == "returned"]
    lost = [p for p in props if p.status != "returned"]
    done = case.tasks.filter(done_at__isnull=False).order_by("done_at")
    issued = case.issued_documents.filter(status="done").order_by("created_at") if hasattr(case, "issued_documents") else []

    values = {
        "วันนี้": d(now.date()),
        "เวลาปัจจุบัน": t(now),
        "วันปัจจุบัน": str(now.day),
        "เดือนปัจจุบัน": thai.MONTHS[now.month - 1],
        "ปีปัจจุบัน": str(now.year + 543),
        "หน่วย": {
            "ชื่อ": unit.name if unit else "",
            "ที่อยู่": unit.address if unit else "",
            "โทรศัพท์": unit.phone if unit else "",
            "บก": unit.division if unit else "",
            "บช": unit.bureau if unit else "",
            "เลขหนังสือ": unit.doc_prefix if unit else "",
        },
        "พงส": {
            "ยศ": owner.rank,
            "ชื่อ": owner.get_full_name(),
            "ยศชื่อ": owner.display_name if owner.rank else "",
            "ตำแหน่ง": owner.position,
            "โทรศัพท์": owner.phone,
        },
        "ผบ": {
            "ยศชื่อ": f"{commander.rank}{commander.name}" if commander else "",
            "ตำแหน่ง": commander.position if commander else "",
        },
        "ศาล": {"ชื่อ": next((c.name for c in courts if c.kind == "court"), "")},
        "อัยการ": {"ชื่อ": next((c.name for c in courts if c.kind == "prosecutor"), "")},
        "คดี": {
            "เลขคดี": case.case_no,
            "เลขรับแจ้ง": case.receipt_no,
            "เรื่อง": case.title,
            "ประเภท": case.get_case_type_display(),
            "ฐานความผิด": case.offense,
            "มาตรา": case.law_sections,
            "อัตราโทษ": case.penalty_text,
            "ศาล": case.get_court_type_display() if case.court_type else "",
            "เขตอำนาจ": case.jurisdiction,
            "วันรับแจ้ง": d(case.reported_at),
            "เวลารับแจ้ง": t(case.reported_at),
            "วันเกิดเหตุ": d(case.incident_from),
            "เวลาเกิดเหตุ": t(case.incident_from),
            "ถึงวันเกิดเหตุ": d(case.incident_to),
            "สถานที่เกิดเหตุ": case.location,
            "พฤติการณ์": case.circumstances,
            "ความเสียหาย": money(case.damage_amount) if case.damage_amount else "",
        },
        "ปจว": {
            "ข้อ": case.dr_complaint_item,
            "เลขที่": case.dr_complaint_no,
            "วันที่": d(case.dr_complaint_at),
            "เวลา": t(case.dr_complaint_at),
            "ตัดเลขข้อ": case.dr_caseno_item,
            "ตัดเลขวันที่": d(case.dr_caseno_at),
        },
        "ผู้กล่าวหา": party_values(accuser),
        "บุคคล": party_values(party) if party else {},
        "รายชื่อผู้ต้องหา": "\n".join(f"{i}. {p.person.full_name}" for i, p in enumerate(suspects, start=1)),
        "รายชื่อพยาน": "\n".join(f"{i}. {p.person.full_name}" for i, p in enumerate(witnesses, start=1)),
        "จำนวนผู้ต้องหา": len(suspects),
        "จำนวนพยาน": len(witnesses),
        "ทรัพย์": property_rows(props),
        "ทรัพย์ได้คืน": property_rows(returned),
        "ทรัพย์ไม่ได้คืน": property_rows(lost),
        "รวมทรัพย์": money(sum((p.value for p in props), Decimal(0))),
        "รวมทรัพย์ได้คืน": money(sum((p.value for p in returned), Decimal(0))),
        "รวมทรัพย์ไม่ได้คืน": money(sum((p.value for p in lost), Decimal(0))),
        "ของกลาง": [],  # evidence register arrives in phase 10
        "บันทึก": [{"วันที่": d(x.done_at), "รายการ": x.title, "อ้างอิง": x.reason} for x in done],
        "เอกสารในสำนวน": [{"ชื่อ": x.title, "วันที่": d(x.created_at), "แผ่น": ""} for x in issued],
        "ฎีกาที่อ้าง": "\n".join(
            f"- ฎีกาที่ {p.ruling.number} ({p.ruling.sections}) {p.ruling.summary[:300]}"
            for p in case.pinned_rulings.select_related("ruling")
        )
        or "-",
        "ความคืบหน้า": {"ครั้งที่": "2" if case.tasks.filter(rule_key="progress_1", done_at__isnull=False).exists() else "1"},
    }
    if letter is not None:
        from cases.letters import LETTER_TEMPLATES

        tpl = LETTER_TEMPLATES.get(letter.template, {"fields": []})
        lines = []
        for key, label, kind in tpl["fields"]:
            v = letter.fields.get(key)
            if v in (None, "", False):
                continue
            if kind == "checkbox":
                lines.append(label)
            elif kind == "date":
                lines.append(f"{label} {d(date.fromisoformat(v))}")
            elif kind == "datetime-local":
                lines.append(f"{label} {d(datetime.fromisoformat(v))} เวลา {datetime.fromisoformat(v):%H.%M} นาฬิกา")
            else:
                lines.append(f"{label} {v}")
        values["หนังสือ"] = {
            "เลขที่": letter.number,
            "เรื่อง": letter.subject,
            "เรียน": letter.recipient,
            "วันที่": d(letter.issued_at) if letter.issued_at else d(now.date()),
            "รายละเอียด": "\n".join(f"{i}. {x}" for i, x in enumerate(lines, start=1)),
        }
    return values
