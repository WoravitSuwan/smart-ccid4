"""แดชบอร์ด (B7): ส่วนตัว · หน่วยงาน · รายงานประจำเดือน."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import OuterRef, Q, Subquery, Sum
from django.utils import timezone

from cases.models import Case, CaseStatus
from core import thai

ZERO = Decimal("0")
STALE_DAYS = 30


def _prefetch(qs):
    return qs.select_related("owner").prefetch_related("parties__person", "parties__detentions", "tasks")


def deadline_rows(cases, now=None) -> list[tuple]:
    from deadlines.services import case_deadlines, load_rules

    rs = load_rules()
    rows = []
    for c in cases:
        for it in case_deadlines(c, now, rs):
            rows.append((c, it))
    rows.sort(key=lambda r: r[1].deadline.sort_key())
    return rows


# ---------------------------------------------------------------- ส่วนตัว
def personal(user, now=None) -> dict:
    from deadlines.models import Task
    from interviews.models import InterviewSession
    from parties.models import Statement

    now = now or timezone.now()
    cases = list(
        _prefetch(Case.objects.alive().filter(Q(owner=user) | Q(shared_with=user)).exclude(status=CaseStatus.CLOSED).distinct())
    )
    rows = deadline_rows(cases, now)
    overdue = [r for r in rows if r[1].status.days_left < 0]
    week = [r for r in rows if 0 <= r[1].status.days_left <= 7]
    today = [r for r in rows if r[1].status.days_left == 0]
    stale = sorted((c for c in cases if c.updated_at < now - timedelta(days=STALE_DAYS)), key=lambda c: c.updated_at)
    latest = Statement.objects.filter(party=OuterRef("party")).order_by("-version").values("pk")[:1]
    drafts = list(
        Statement.objects.filter(party__case__in=cases, pk=Subquery(latest), confirmed_at__isnull=True)
        .exclude(source=Statement.Source.MANUAL)
        .select_related("party__person", "party__case")
    )
    reviews = list(
        InterviewSession.objects.filter(case__in=cases, status=InterviewSession.Status.REVIEW).select_related(
            "party__person", "case"
        )
    )
    tasks = list(
        Task.objects.filter(case__in=cases, done_at__isnull=True)
        .exclude(source=Task.Source.AUTO)
        .select_related("case")
        .order_by("created_at")[:15]
    )
    return {"cases": cases, "overdue": overdue, "week": week, "today": today, "stale": stale[:15], "stale_count": len(stale),
            "drafts": drafts, "reviews": reviews, "tasks": tasks, "todo_count": len(today) + len(tasks),
            "review_count": len(drafts) + len(reviews)}  # fmt: skip


# ---------------------------------------------------------------- หน่วยงาน
def scope(user):
    """หัวหน้า = ทั้งกลุ่มงาน · ผู้บังคับบัญชา/ผู้ดูแล = ทั้งหน่วย (ตัวเลขรวม ไม่เปิดเนื้อหาคดี)."""
    if user.role == "supervisor":
        return Case.objects.visible_to(user)
    return Case.objects.alive()


@dataclass
class Officer:
    name: str
    open: int = 0
    closed: int = 0
    received: int = 0  # in the period
    overdue: int = 0
    week: int = 0
    cases_overdue: set = field(default_factory=set)


def month_start(d: date, back: int = 0) -> date:
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def damage_of(c) -> Decimal:
    return max(c.damage_amount or ZERO, getattr(c, "prop_sum", None) or ZERO)


def mule_accounts(cases, limit=10) -> list[dict]:
    """บัญชีปลายทางที่พบในหลายคดีมากที่สุด (แสดงแบบปิดบัง)."""
    from cyber import identifiers
    from cyber.models import BankAccount

    agg: dict[str, dict] = defaultdict(
        lambda: {"cases": set(), "received": ZERO, "banks": set(), "masked": "", "status": Counter()}
    )
    accounts = BankAccount.objects.filter(case__in=cases, is_victim=False).annotate(rcv=Sum("incoming__amount"))
    for a in accounts:
        key = identifiers.digest("bank_account", a.norm)
        row = agg[key]
        row["cases"].add(a.case_id)
        row["received"] += a.rcv or ZERO
        if a.bank:
            row["banks"].add(a.bank)
        row["masked"] = a.masked
        row["status"][a.get_status_display()] += 1
    rows = [
        {"masked": r["masked"], "cases": len(r["cases"]), "received": r["received"], "banks": ", ".join(sorted(r["banks"])) or "-",
         "status": ", ".join(f"{k} {v}" for k, v in r["status"].most_common())}
        for r in agg.values() if len(r["cases"]) >= 2
    ]  # fmt: skip
    rows.sort(key=lambda r: (-r["cases"], -r["received"]))
    return rows[:limit]


def unit(user, month: date | None = None, now=None) -> dict:
    now = now or timezone.now()
    today = timezone.localdate()
    month = month or date(today.year, today.month, 1)
    end = next_month(month)
    base = scope(user)
    cases = list(_prefetch(base.annotate(prop_sum=Sum("properties__value"))))
    open_cases = [c for c in cases if c.status != CaseStatus.CLOSED]
    rows = deadline_rows(open_cases, now)
    officers: dict[int, Officer] = {}
    for c in cases:
        o = officers.setdefault(c.owner_id, Officer(c.owner.display_name))
        o.closed += c.status == CaseStatus.CLOSED
        o.open += c.status != CaseStatus.CLOSED
        o.received += month <= timezone.localtime(c.reported_at).date() < end
    for c, it in rows:
        o = officers[c.owner_id]
        if it.status.days_left < 0:
            o.overdue += 1
            o.cases_overdue.add(c.pk)
        elif it.status.days_left <= 7:
            o.week += 1
    in_month = [c for c in cases if month <= timezone.localtime(c.reported_at).date() < end]
    months = [month_start(month, back) for back in range(11, -1, -1)]
    per_month = Counter((timezone.localtime(c.reported_at).year, timezone.localtime(c.reported_at).month) for c in cases)
    offenses = Counter((c.offense or "ยังไม่ระบุฐานความผิด").strip() for c in in_month or cases)
    return {
        "month": month, "month_label": f"{thai.MONTHS[month.month - 1]} {month.year + 543}", "total": len(cases), "open": len(open_cases),
        "received_month": len(in_month), "closed": sum(1 for c in cases if c.status == CaseStatus.CLOSED),
        "overdue": sum(1 for r in rows if r[1].status.days_left < 0), "overdue_cases": len({r[0].pk for r in rows if r[1].status.days_left < 0}),
        "damage_month": sum((damage_of(c) for c in in_month), ZERO), "damage_all": sum((damage_of(c) for c in cases), ZERO),
        "by_type": Counter(c.get_case_type_display() for c in in_month).most_common(),
        "officers": sorted(officers.values(), key=lambda o: (-o.overdue, -o.open, o.name)),
        "monthly": [(f"{thai.MONTHS_SHORT[m.month - 1]} {str(m.year + 543)[2:]}", per_month.get((m.year, m.month), 0), m) for m in months],
        "offenses": offenses.most_common(10), "offense_scope": "ในเดือนนี้" if in_month else "ทั้งหมด",
        "mules": mule_accounts(base),
    }  # fmt: skip
