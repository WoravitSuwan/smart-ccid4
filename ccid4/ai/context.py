"""What the language model is allowed to see about a case: facts needed for the task, no citizen IDs or account numbers."""

from documents.context import d, money, t


def person(cp, with_statement=True) -> dict | None:
    if cp is None:
        return None
    p = cp.person
    st = cp.current_statement if with_statement else None
    out = {"ชื่อ": p.full_name, "อาชีพ": p.occupation, "บทบาท": cp.get_role_display()}
    if st:
        out["คำให้การ"] = st.text
    return out


def case_context(case, with_statements=True) -> dict:
    parties = list(case.parties.select_related("person"))
    acc = next((p for p in parties if p.role == "accuser"), None)
    return {
        "คดี": {
            "ประเภท": case.get_case_type_display(),
            "เรื่อง": case.title,
            "ฐานความผิดเดิม": case.offense,
            "มาตรา": case.law_sections,
            "วันเกิดเหตุ": d(case.incident_from),
            "เวลาเกิดเหตุ": t(case.incident_from),
            "สถานที่เกิดเหตุ": case.location,
            "วันรับแจ้ง": d(case.reported_at),
            "ความเสียหาย": money(case.damage_amount) if case.damage_amount else "",
            "พฤติการณ์": case.circumstances,
        },
        "ผู้กล่าวหา": person(acc, with_statements),
        "ผู้ต้องหา": [person(p, with_statements) for p in parties if p.role == "suspect"],
        "พยาน": [person(p, with_statements) for p in parties if p.role == "witness"],
        "ทรัพย์": [{"ชื่อ": i.name, "มูลค่า": money(i.value), "สถานะ": i.get_status_display()} for i in case.properties.all()],
    }
