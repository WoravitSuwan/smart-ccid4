"""Saving the people of a case. Called from cases.services.save_case inside one transaction."""

from audit.services import log

from .forms import PERSON_FIELDS
from .models import CaseParty, Person, Statement

PARTY_FIELDS = ["arrested_at", "arrived_at", "custody_place", "bail_status"]


def party_initial(cp: CaseParty) -> dict:
    p = cp.person
    data = {f: getattr(p, f) for f in PERSON_FIELDS}
    data.update({f: getattr(cp, f) for f in PARTY_FIELDS})
    data["marks"] = p.marks
    data["citizen_id"] = p.masked_id
    data["party_id"] = cp.pk
    st = cp.current_statement
    data["statement"] = st.text if st else ""
    return data


def save_statement(user, cp: CaseParty, text: str, source=Statement.Source.MANUAL) -> Statement | None:
    """Every change becomes a new version; identical text is not saved again."""
    text = (text or "").strip()
    current = cp.current_statement
    if not text or (current and current.text == text):
        return None
    st = Statement.objects.create(
        party=cp, version=(current.version + 1) if current else 1, text=text, source=source, created_by=user
    )
    log(user, "edit", "statement", st.pk, f"คำให้การ{cp.get_role_display()} {cp.person} ฉบับที่ {st.version}")
    return st


def save_party(user, case, role: str, order: int, data: dict) -> CaseParty | None:
    """Create or update one person of the case from cleaned form data. Returns None when the form was blank or deleted."""
    pid = data.get("party_id")
    cp = case.parties.select_related("person").filter(pk=pid, role=role).first() if pid else None
    if data.get("DELETE") or data.get("is_blank"):
        if cp:
            delete_party(user, cp)
        return None
    person = cp.person if cp else Person()
    changed = []
    for f in PERSON_FIELDS + ["marks"]:
        new = data.get(f) if data.get(f) is not None else ("" if f != "birth_date" else None)
        if getattr(person, f) != new:
            changed.append(f)
            setattr(person, f, new)
    if changed or not person.pk:
        person.save()
    if not cp:
        cp = CaseParty(case=case, person=person, role=role)
    for f in PARTY_FIELDS:
        new = data.get(f) if data.get(f) is not None else ("" if f in ("custody_place", "bail_status") else None)
        if getattr(cp, f) != new:
            changed.append(f)
            setattr(cp, f, new)
    created = not cp.pk
    if created or changed or cp.order != order:
        cp.order = order
        cp.save()
    if created:
        log(user, "create", "party", cp.pk, f"เพิ่ม{cp.get_role_display()} {person} ในคดี {case}")
    elif changed:
        log(user, "edit", "party", cp.pk, f"แก้ไข{cp.get_role_display()} {person} ในคดี {case}: {', '.join(sorted(set(changed)))}")
    save_statement(user, cp, data.get("statement", ""))
    return cp


def delete_party(user, cp: CaseParty) -> None:
    person = cp.person
    log(user, "delete", "party", cp.pk, f"ลบ{cp.get_role_display()} {person} ออกจากคดี {cp.case}")
    cp.delete()
    if not person.roles.exists():
        person.delete()
