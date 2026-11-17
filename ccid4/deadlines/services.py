"""Glue between the database and deadlines/rules.py: load rules, compute a case's deadlines, keep tasks in sync."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.services import log
from core import thai

from . import rules
from .models import DeadlineRule, Detention, Holiday, Task

CACHE_KEY = "deadlines:ruleset"


@dataclass
class RuleSet:
    params: dict
    holidays: frozenset

    @property
    def is_holiday(self):
        return rules.holiday_checker(self.holidays, weekends=self.params["general"].get("weekends_are_holidays", True))

    @property
    def warn_days(self) -> int:
        return int(self.params["general"].get("warn_days", 3))


def load_rules() -> RuleSet:
    cached = cache.get(CACHE_KEY)
    if cached:
        return cached
    params = {k: dict(v) for k, v in rules.DEFAULTS.items()}
    for r in DeadlineRule.objects.filter(active=True):
        params[r.key] = params.get(r.key, {}) | r.params
    rs = RuleSet(params, frozenset(Holiday.objects.values_list("date", flat=True)))
    cache.set(CACHE_KEY, rs, 300)
    return rs


def clear_cache(**kwargs) -> None:
    cache.delete(CACHE_KEY)


@dataclass
class Item:
    deadline: rules.Deadline
    status: rules.Status


# ---------------------------------------------------------------- per-suspect custody
@dataclass
class Custody:
    party: object
    kind: str  # remand | delay
    rule_key: str | None
    periods: list
    plan: rules.Plan | None
    deadline: rules.Deadline | None
    missing_penalty: bool = False
    item: Item | None = None


def custody_for(party, case, rs: RuleSet | None = None) -> Custody:
    rs = rs or load_rules()
    dets = [d for d in party.detentions.all()]
    kind = "delay" if case.court_type == "magistrate" else "remand"
    dets_kind = [d for d in dets if d.kind == kind]
    periods = [rules.Period(d.start_date, d.end_date) for d in dets_kind]
    name = party.person.full_name
    if kind == "delay":
        key = "delay_magistrate"
    else:
        key = rules.penalty_band(case.max_penalty_months, case.max_fine, rs.params)
    plan = rules.plan_periods(key, rs.params[key], periods, rs.is_holiday) if key else None
    dl = None
    if periods and plan:
        n = len(periods)
        word = "ผัดฟ้อง" if kind == "delay" else "ฝากขัง"
        if plan.exhausted:
            label = f"{name}: ครบอำนาจ{word}แล้ว ต้องฟ้องหรือปล่อยตัว"
        else:
            label = f"{name}: {word}ผัด {n} ครบ (เหลือ {plan.remaining_times} ผัด {plan.remaining_days} วัน)"
        dl = rules.Deadline(key, label, plan.current_end, plan.act_by, plan.ref, party.pk, short=f"{word}ผัด {n}")
    elif party.bail_status:
        dl = None  # released on bail: no custody clock
    elif kind == "delay" and party.arrested_at:
        dl = rules.file_deadline(party.arrested_at, rs.params["delay_magistrate"], party.pk)
        dl.label, dl.short = f"{name}: {dl.label}", "ยื่นฟ้อง/ผัดฟ้อง"
    elif kind == "remand" and party.arrived_at:
        dl = rules.custody_deadline(party.arrived_at, rs.params["custody"], party.pk)
        dl.label, dl.short = f"{name}: {dl.label} (48 ชม.)", "ควบคุมตัว 48 ชม."
    item = Item(dl, rules.status(dl, timezone.now(), rs.warn_days)) if dl else None
    return Custody(party, kind, key, dets_kind, plan, dl, missing_penalty=(kind == "remand" and key is None), item=item)


def record_detention(user, case, party, start: date, days: int, court="", order_no="", note="") -> Detention:
    """Save one remand/delay period after checking it against the rules (A5: when a remand is recorded...)."""
    rs = load_rules()
    c = custody_for(party, case, rs)
    if c.plan is None:
        raise ValidationError("ระบุโทษจำคุกหรือโทษปรับสูงสุดในแท็บข้อมูลคดีก่อน จึงคำนวณฝากขังได้")
    p = rs.params[c.rule_key]
    if days < 1 or days > p["per_days"]:
        raise ValidationError(f"ผัดหนึ่งได้ไม่เกิน {p['per_days']} วัน")
    if c.plan.used_times and c.plan.exhausted:
        raise ValidationError("ใช้สิทธิ์ผัดครบแล้ว")
    period = rules.Period(start, rules.add_days(start, days, p.get("count_first_day", True)))
    check = rules.plan_periods(
        c.rule_key, p, [*[rules.Period(d.start_date, d.end_date) for d in c.periods], period], rs.is_holiday
    )
    if check.problems:
        raise ValidationError(check.problems)
    with transaction.atomic():
        det = Detention.objects.create(
            party=party,
            kind=c.kind,
            start_date=period.start,
            end_date=period.end,
            court=court,
            order_no=order_no,
            note=note,
            created_by=user,
        )
        n = len(c.periods) + 1
        word = det.get_kind_display()
        text = f"{word}ผัดที่ {n} {party.person.full_name} {thai.format_date(period.start)} – {thai.format_date(period.end)} ({days} วัน)"
        log(user, "create", "detention", det.pk, f"{text} คดี {case}")
        log_action(case, user, text, reason=p.get("ref", ""))
        sync_rule_tasks(case)
    return det


def delete_detention(user, det: Detention) -> None:
    case = det.party.case
    log(
        user,
        "delete",
        "detention",
        det.pk,
        f"ลบ{det.get_kind_display()} {det.party.person} {det.start_date}–{det.end_date} คดี {case}",
    )
    det.delete()
    sync_rule_tasks(case)


# ---------------------------------------------------------------- case deadlines
def case_deadlines(case, now=None, rs: RuleSet | None = None) -> list[Item]:
    """Section 1 of the right panel: legal deadlines with remaining time and colour."""
    if case.status == "closed" or case.deleted_at:
        return []
    rs = rs or load_rules()
    now = now or timezone.now()
    out: list[rules.Deadline] = []
    suspects = [p for p in case.parties.all() if p.role == "suspect"]
    for party in suspects:
        c = custody_for(party, case, rs)
        if c.deadline:
            out.append(c.deadline)
    start = case.dr_complaint_at or case.reported_at
    done = {t.rule_key for t in case.tasks.all() if t.done_at}
    if start:
        for key in ("progress_1", "progress_2"):
            if key not in done:
                dl = rules.days_after(start, rs.params[key], rs.is_holiday, key)
                dl.short = "แจ้งความคืบหน้า" + ("ครั้งที่ 1" if key == "progress_1" else "ครั้งที่ 2")
                out.append(dl)
        if not suspects and "unknown_offender" not in done:
            dl = rules.days_after(start, rs.params["unknown_offender"], rs.is_holiday, "unknown_offender")
            dl.short = "ความเห็นคดีไม่ปรากฏตัวผู้กระทำผิด"
            out.append(dl)
    out += summons_deadlines(case)
    out.sort(key=rules.Deadline.sort_key)
    return [Item(d, rules.status(d, now, rs.warn_days)) for d in out]


def summons_deadlines(case) -> list[rules.Deadline]:
    """B1: appointments from summons that are still waiting for the accuser (shown in the case panel)."""
    try:
        reg = case.register
    except ObjectDoesNotExist:  # case not in the unit register
        return []
    out = []
    for s in reg.summons.filter(result="issued").order_by("number"):
        at = timezone.localtime(s.appointment_at)
        out.append(rules.Deadline("summons", f"นัดผู้กล่าวหาตามหมายเรียกครั้งที่ {s.number}", at, at, "ป.วิ.อาญา ม.52",
                                  short=f"นัดตามหมายเรียกครั้งที่ {s.number}"))  # fmt: skip
    return out


def nearest(case, now=None) -> Item | None:
    items = case_deadlines(case, now)
    return items[0] if items else None


# ---------------------------------------------------------------- tasks (สิ่งที่ต้องทำ / ทำแล้ว / บันทึกพนักงานสอบสวน)
def desired_rule_tasks(case) -> dict[str, tuple[str, str, bool]]:
    """rule_key -> (title, reason, already satisfied?)."""
    from parties.models import CaseParty

    parties = list(CaseParty.objects.filter(case=case).select_related("person"))  # fresh: may run right after a save
    acc = next((p for p in parties if p.role == "accuser"), None)
    out = {
        "dr_complaint": ("ลงบันทึกประจำวันรับคำร้องทุกข์", "ต้องมีก่อนออกเอกสาร (A4.2)", bool(case.dr_complaint_no)),
        "penalty": (
            "ระบุฐานความผิดและโทษสูงสุด",
            "ใช้คำนวณกำหนดฝากขัง (A5)",
            bool(case.offense and (case.max_penalty_months is not None or case.max_fine is not None)),
        ),
        "accuser_statement": ("สอบปากคำผู้กล่าวหา", "ป.วิ.อาญา ม.131", bool(acc and acc.current_statement)),
    }
    if case.case_type == "online":
        out["bank_letter"] = ("ทำหนังสือขอข้อมูลบัญชีธนาคาร/ขออายัด", "คดีออนไลน์", case.letters.filter(template="bank").exists())
    else:
        out["scene"] = ("ตรวจสถานที่เกิดเหตุ", "ป.วิ.อาญา ม.131", False)
    for p in parties:
        if p.role != "suspect":
            continue
        name = p.person.full_name
        out[f"suspect_statement:{p.pk}"] = (f"สอบคำให้การผู้ต้องหา {name}", "ป.วิ.อาญา ม.134", bool(p.current_statement))
        if p.arrived_at or p.arrested_at:
            out[f"rights:{p.pk}"] = (
                f"แจ้งสิทธิผู้ต้องหา {name}",
                "ป.วิ.อาญา ม.7/1, ม.134/1, ม.134/3, ม.134/4",
                bool(p.rights_informed_at),
            )
    rs = load_rules()
    for key in ("progress_1", "progress_2"):
        out[key] = (rs.params[key]["name"], rs.params[key]["ref"], False)
    return out


@transaction.atomic
def sync_rule_tasks(case) -> None:
    """Create missing rule tasks, tick ones the system can see are done, and drop ones that no longer apply."""
    want = desired_rule_tasks(case)
    have = {t.rule_key: t for t in case.tasks.select_for_update().exclude(rule_key="")}
    now = timezone.now()
    for key, (title, reason, satisfied) in want.items():
        t = have.get(key)
        if t is None:
            Task.objects.create(
                case=case, title=title, reason=reason, source=Task.Source.RULE, rule_key=key, done_at=now if satisfied else None
            )
            continue
        changed = t.title != title
        t.title = title
        if satisfied and not t.done_at:
            t.done_at, t.done_by, changed = now, None, True
        elif not satisfied and t.done_at and t.done_by_id is None and t.source == Task.Source.RULE and key not in ("scene",):
            t.done_at, changed = None, True  # the system ticked it; the reason went away again
        if changed:
            t.save()
    for key, t in have.items():
        if key not in want and not t.done_at:
            t.delete()


def log_action(case, user, title: str, reason: str = "") -> Task:
    """Automatic entry in the investigator log (A4.6): shows under ทำแล้ว and in the Timeline."""
    return Task.objects.create(
        case=case, title=title, reason=reason, source=Task.Source.AUTO, done_at=timezone.now(), done_by=user, created_by=user
    )


def add_task(case, user, title: str, reason: str = "", done: bool = False) -> Task:
    t = Task.objects.create(
        case=case, title=title, reason=reason, source=Task.Source.MANUAL, created_by=user,
        done_at=timezone.now() if done else None, done_by=user if done else None,
    )  # fmt: skip
    log(user, "create", "task", t.pk, f"เพิ่มรายการ '{title}' คดี {case}")
    return t


def set_done(task: Task, user, done: bool) -> None:
    task.done_at = timezone.now() if done else None
    task.done_by = user if done else None
    task.save(update_fields=["done_at", "done_by"])
    log(user, "edit", "task", task.pk, f"{'ทำแล้ว' if done else 'ย้ายกลับไปสิ่งที่ต้องทำ'}: {task.title} คดี {task.case}")


def today_local() -> date:
    return timezone.localdate()


def suggested_start(custody: Custody) -> date:
    if custody.periods:
        return custody.periods[-1].end_date + timedelta(days=1)
    when = custody.party.arrived_at or custody.party.arrested_at
    return timezone.localtime(when).date() if when else today_local()


def panel_context(case) -> dict:
    tasks = list(case.tasks.select_related("done_by").all())
    todo = [t for t in tasks if not t.done_at]
    done = sorted((t for t in tasks if t.done_at), key=lambda t: t.done_at, reverse=True)
    return {"deadline_items": case_deadlines(case), "todo": todo, "done": done}
