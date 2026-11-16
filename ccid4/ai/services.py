"""AI tasks for the case page (phase 5). Every result is a draft: the officer edits or confirms it."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from audit.services import log
from parties.models import CaseParty, Statement

from . import llm
from .context import case_context

PLEAS = {"confess": "รับสารภาพ", "deny": "ปฏิเสธ", "later": "ให้การภายหลังในชั้นศาล"}
ROLE_LABELS = {"accuser": "ผู้กล่าวหา", "witness": "พยาน", "suspect": "ผู้ต้องหา"}
INSTRUCTIONS = {
    "accuser": "- เล่าตามลำดับเวลาว่าเกิดอะไรขึ้น ความเสียหาย และความประสงค์ให้ดำเนินคดี",
    "witness": "- ให้สอดคล้องกับพฤติการณ์แห่งคดี เล่าเฉพาะสิ่งที่พยานรู้เห็น ถ้าข้อมูลไม่บอกว่าพยานเห็นอะไร ให้เว้น ……",
    "confess": "- ผู้ต้องหาให้การรับสารภาพ เล่าการกระทำตามพฤติการณ์แห่งคดี",
    "deny": "- ผู้ต้องหาให้การปฏิเสธ ให้มีข้อความปฏิเสธชัดเจน และเว้น …… สำหรับข้อต่อสู้ที่ผู้ต้องหาจะเล่าเอง",
    "later": "- ผู้ต้องหาขอให้การในชั้นศาล ให้สั้นและไม่มีรายละเอียดข้อเท็จจริง",
}
CHARGE_FIELDS = ["offense", "law_sections", "penalty_text", "max_penalty_months", "max_fine", "court_type", "jurisdiction"]


def analyze_charges(case, user) -> dict:
    out = llm.complete_json("analyze_charges", case_context(case), user=user, case=case)
    log(user, "edit", "case", case.pk, f"ให้ AI วิเคราะห์ข้อหา คดี {case} (ยังไม่บันทึกจนกว่าผู้ใช้จะกดใช้และบันทึก)")
    return out


def summarize(case, user) -> str:
    out = llm.complete_json("summarize_case", case_context(case), user=user, case=case)
    return llm.clean_text(out["summary"])


@transaction.atomic
def draft_statement(cp: CaseParty, user, plea: str | None = None) -> Statement:
    from deadlines.services import log_action, sync_rule_tasks
    from parties.services import save_statement

    case = cp.case
    key = plea if cp.role == "suspect" else cp.role
    ctx = case_context(case, with_statements=cp.role != "suspect") | {
        "บุคคล": {"ชื่อ": cp.person.full_name, "อาชีพ": cp.person.occupation}
    }
    extra = {
        "role": cp.role,
        "plea": plea,
        "role_label": ROLE_LABELS[cp.role],
        "instruction": INSTRUCTIONS.get(key or "deny", ""),
    }
    out = llm.complete_json("draft_statement", ctx, user=user, case=case, extra=extra)
    st = save_statement(user, cp, llm.clean_text(out["text"]), source=Statement.Source.AI)
    if st is None:  # same text as the current version
        st = cp.current_statement
    log_action(case, user, f"AI ร่างคำให้การ{cp.get_role_display()} {cp.person.full_name} ฉบับที่ {st.version} (รอตรวจ)")
    sync_rule_tasks(case)
    return st


def confirm_statement(st: Statement, user) -> None:
    from deadlines.services import log_action

    st.confirmed_by, st.confirmed_at = user, timezone.now()
    st.save(update_fields=["confirmed_by", "confirmed_at"])
    cp = st.party
    log(user, "confirm", "statement", st.pk, f"ยืนยันคำให้การ{cp.get_role_display()} {cp.person} ฉบับที่ {st.version} คดี {cp.case}")
    log_action(cp.case, user, f"ตรวจและยืนยันคำให้การ{cp.get_role_display()} {cp.person.full_name} ฉบับที่ {st.version}")


@transaction.atomic
def inform_rights(cp: CaseParty, user, informed_at, plea: str, draft: bool = True) -> Statement | None:
    """A4.4 modal: record that rights were given, the plea, then let AI draft the statement for that plea."""
    from deadlines.services import log_action, sync_rule_tasks

    cp.rights_informed_at, cp.plea = informed_at, plea
    cp.save(update_fields=["rights_informed_at", "plea"])
    log(user, "edit", "party", cp.pk, f"แจ้งสิทธิผู้ต้องหา {cp.person} แนวคำให้การ {PLEAS[plea]} คดี {cp.case}")
    log_action(
        cp.case,
        user,
        f"แจ้งข้อกล่าวหาและสิทธิ {cp.person.full_name} · ให้การ{PLEAS[plea]}",
        reason="ป.วิ.อาญา ม.7/1, ม.134/1, ม.134/3, ม.134/4",
    )
    sync_rule_tasks(cp.case)
    return draft_statement(cp, user, plea) if draft else None


def check_consistency(cp: CaseParty, user) -> list[dict]:
    st = cp.current_statement
    if not st:
        return []
    case = cp.case
    ctx = {"พฤติการณ์": case.circumstances, "คำให้การ": st.text, "ข้อเท็จจริง": case_context(case, False)["คดี"]}
    return llm.complete_json("check_consistency", ctx, user=user, case=case)["flags"]
