"""กำหนดเวลาตามกฎหมาย (A5). Pure functions only: no database, no clock (the caller passes `now`).

Parameters come from `DeadlineRule` rows (see services.load_rules); DEFAULTS below are the starting values and
must be reviewed by the unit's legal officer before real use. All times are handled in Thai time (UTC+7).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Bangkok")

# holiday policy: "none" = the date stands; "before" = act on the last working day on/before it (court closed);
# "after" = the duty moves to the next working day.
DEFAULTS: dict[str, dict] = {
    "custody": {"name": "นำตัวผู้ถูกจับส่งศาล", "hours": 48, "holiday": "none", "ref": "ป.วิ.อาญา ม.87"},
    "remand_minor": {
        "name": "ฝากขัง (โทษจำคุกไม่เกิน 6 เดือน หรือปรับไม่เกิน 500 บาท)",
        "max_months": 6, "max_fine": 500, "per_days": 7, "max_times": 1, "total_days": 7,
        "count_first_day": True, "holiday": "before", "ref": "ป.วิ.อาญา ม.87 วรรคห้า",
    },
    "remand_mid": {
        "name": "ฝากขัง (โทษจำคุกเกิน 6 เดือนแต่ไม่ถึง 10 ปี หรือปรับเกิน 500 บาท)",
        "per_days": 12, "max_times": None, "total_days": 48,
        "count_first_day": True, "holiday": "before", "ref": "ป.วิ.อาญา ม.87 วรรคหก",
    },
    "remand_major": {
        "name": "ฝากขัง (โทษจำคุก 10 ปีขึ้นไป)",
        "min_months": 120, "per_days": 12, "max_times": None, "total_days": 84,
        "count_first_day": True, "holiday": "before", "ref": "ป.วิ.อาญา ม.87 วรรคเจ็ด",
    },
    "delay_magistrate": {
        "name": "ผัดฟ้องคดีศาลแขวง", "file_hours": 48, "per_days": 6, "max_times": 5, "total_days": 30,
        "count_first_day": True, "holiday": "before", "ref": "พ.ร.บ.จัดตั้งศาลแขวงฯ ม.7",
    },
    "progress_1": {"name": "แจ้งความคืบหน้าแก่ผู้เสียหาย ครั้งที่ 1", "days": 30, "holiday": "after", "ref": "ตร.419"},
    "progress_2": {"name": "แจ้งความคืบหน้าแก่ผู้เสียหาย ครั้งที่ 2", "days": 60, "holiday": "after", "ref": "ตร.419"},
    "unknown_offender": {
        "name": "พิจารณาความเห็นคดีไม่ปรากฏตัวผู้กระทำผิด", "days": 365, "holiday": "after", "ref": "ป.วิ.อาญา ม.140",
    },
    "general": {"name": "ค่าทั่วไป", "warn_days": 3, "weekends_are_holidays": True},
}  # fmt: skip


# ---------------------------------------------------------------- time helpers
def to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("ต้องเป็นเวลาที่ระบุเขตเวลา")
    return dt.astimezone(TZ)


def local_date(value: date | datetime) -> date:
    return to_local(value).date() if isinstance(value, datetime) else value


def add_days(start: date, days: int, count_first_day: bool = True) -> date:
    """End date of a period of `days` days. Counting the first day: 12 days from 1 Jan end on 12 Jan."""
    return start + timedelta(days=days - 1 if count_first_day else days)


def period_days(start: date, end: date) -> int:
    """Days in an inclusive period."""
    return (end - start).days + 1


def holiday_checker(holidays: Iterable[date], weekends: bool = True) -> Callable[[date], bool]:
    days = set(holidays)
    return lambda d: d in days or (weekends and d.weekday() >= 5)


def adjust(d: date, policy: str, is_holiday: Callable[[date], bool]) -> date:
    if policy not in ("before", "after"):
        return d
    step = timedelta(days=-1 if policy == "before" else 1)
    for _ in range(60):
        if not is_holiday(d):
            return d
        d += step
    raise ValueError("ตั้งวันหยุดติดกันเกิน 60 วัน")


# ---------------------------------------------------------------- result types
@dataclass
class Deadline:
    key: str
    label: str
    due: date | datetime  # the legal limit
    act_by: date | datetime  # when the officer must act (after holiday policy)
    ref: str = ""
    party_id: int | None = None
    note: str = ""
    short: str = ""  # for the chip in the case header, e.g. "ฝากขังผัด 2"

    @property
    def is_datetime(self) -> bool:
        return isinstance(self.due, datetime)

    def sort_key(self) -> datetime:
        return to_local(self.act_by) if self.is_datetime else datetime.combine(self.act_by, datetime.max.time(), TZ)


@dataclass
class Status:
    tone: str  # primary | warning | danger
    text: str
    days_left: int


def status(dl: Deadline, now: datetime, warn_days: int = 3) -> Status:
    now = to_local(now)
    if dl.is_datetime:
        secs = (to_local(dl.act_by) - now).total_seconds()
        if secs < 0:
            h = math.ceil(-secs / 3600)
            return Status("danger", f"เลยกำหนด {h} ชม." if h < 48 else f"เลยกำหนด {h // 24} วัน", -math.ceil(-secs / 86400))
        hours, minutes = int(secs // 3600), int(secs % 3600 // 60)
        text = f"เหลือ {hours} ชม. {minutes} นาที" if hours < 48 else f"เหลือ {hours // 24} วัน {hours % 24} ชม."
        days = int(secs // 86400)
        return Status("warning" if days < warn_days else "primary", text, days)
    days = (dl.act_by - now.date()).days
    if days < 0:
        return Status("danger", f"เลยกำหนด {-days} วัน", days)
    if days == 0:
        return Status("warning", "ครบกำหนดวันนี้", 0)
    return Status("warning" if days <= warn_days else "primary", f"เหลือ {days} วัน", days)


# ---------------------------------------------------------------- custody (48 hours)
def custody_deadline(arrived_at: datetime, p: dict, party_id: int | None = None) -> Deadline:
    """ม.87: bring the arrested person to court within 48 hours of arriving at the investigator's office.
    Hours run through nights and holidays (policy "none")."""
    due = to_local(arrived_at) + timedelta(hours=p["hours"])
    return Deadline("custody", p["name"], due, due, p["ref"], party_id)


def file_deadline(arrested_at: datetime, p: dict, party_id: int | None = None) -> Deadline:
    """ศาลแขวง: file charges within 48 hours of arrest unless the court grants a delay (ผัดฟ้อง)."""
    due = to_local(arrested_at) + timedelta(hours=p["file_hours"])
    return Deadline("file_charge", "ยื่นฟ้องหรือขอผัดฟ้องต่อศาลแขวง", due, due, p["ref"], party_id)


# ---------------------------------------------------------------- remand (ฝากขัง) and delay (ผัดฟ้อง)
def penalty_band(max_months: int | None, max_fine: int | None, rules: dict) -> str | None:
    """Which remand rule applies. None = maximum penalty not entered yet."""
    if max_months is None and max_fine is None:
        return None
    major, minor = rules["remand_major"], rules["remand_minor"]
    if max_months is not None and max_months >= major["min_months"]:
        return "remand_major"
    if (max_months or 0) > minor["max_months"] or (max_fine or 0) > minor["max_fine"]:
        return "remand_mid"
    return "remand_minor"


@dataclass
class Period:
    start: date
    end: date

    @property
    def days(self) -> int:
        return period_days(self.start, self.end)


@dataclass
class Plan:
    rule_key: str
    name: str
    ref: str
    per_days: int
    max_times: int | None
    total_days: int
    used_days: int = 0
    used_times: int = 0
    remaining_days: int = 0
    remaining_times: int = 0
    current_end: date | None = None  # last day of the current period: ask for the next one before it ends
    act_by: date | None = None
    final_day: date | None = None  # last possible day if every remaining period is granted back to back
    problems: list[str] = field(default_factory=list)

    @property
    def exhausted(self) -> bool:
        return self.remaining_times == 0


def plan_periods(rule_key: str, p: dict, periods: list[Period], is_holiday: Callable[[date], bool]) -> Plan:
    """Summarise the periods already granted and what is left (A5: ผัดที่เหลือ วันเหลือรวม วันสุดท้าย)."""
    periods = sorted(periods, key=lambda x: x.start)
    total = p.get("total_days") or p["per_days"] * p["max_times"]
    plan = Plan(rule_key, p["name"], p.get("ref", ""), p["per_days"], p.get("max_times"), total)
    for i, per in enumerate(periods, start=1):
        if per.end < per.start:
            plan.problems.append(f"ผัดที่ {i}: วันสิ้นสุดก่อนวันเริ่ม")
            continue
        if per.days > p["per_days"]:
            plan.problems.append(f"ผัดที่ {i}: {per.days} วัน เกินครั้งละ {p['per_days']} วัน")
        if i > 1:
            prev = periods[i - 2]
            if per.start <= prev.end:
                plan.problems.append(f"ผัดที่ {i}: ทับกับผัดก่อนหน้า")
            elif per.start > prev.end + timedelta(days=1):
                plan.problems.append(f"ผัดที่ {i}: ไม่ต่อเนื่องจากผัดก่อนหน้า")
        plan.used_days += per.days
    plan.used_times = len(periods)
    if plan.used_days > total:
        plan.problems.append(f"รวม {plan.used_days} วัน เกินที่กฎหมายให้ {total} วัน")
    if p.get("max_times") and plan.used_times > p["max_times"]:
        plan.problems.append(f"ผัด {plan.used_times} ครั้ง เกิน {p['max_times']} ครั้ง")
    remaining_days = max(0, total - plan.used_days)
    times = math.ceil(remaining_days / p["per_days"])
    if p.get("max_times"):
        times = min(times, max(0, p["max_times"] - plan.used_times))
    plan.remaining_times = times
    plan.remaining_days = min(remaining_days, times * p["per_days"])
    if periods:
        plan.current_end = periods[-1].end
        plan.act_by = adjust(plan.current_end, p.get("holiday", "none"), is_holiday)
        plan.final_day = plan.current_end + timedelta(days=plan.remaining_days)
    return plan


def next_period(plan: Plan, start: date, p: dict, days: int | None = None) -> Period:
    """The longest period the court may grant next, starting on `start`."""
    if plan.exhausted and plan.used_times:
        raise ValueError("ใช้สิทธิ์ผัดครบแล้ว")
    limit = plan.remaining_days if plan.used_times else min(plan.total_days, p["per_days"])
    days = min(days or p["per_days"], p["per_days"], limit)
    return Period(start, add_days(start, days, p.get("count_first_day", True)))


# ---------------------------------------------------------------- day-based duties
def days_after(start: date | datetime, p: dict, is_holiday: Callable[[date], bool], key: str) -> Deadline:
    """Duties counted in days from a date (the first day is not counted): progress notices, unknown offender."""
    due = local_date(start) + timedelta(days=p["days"])
    return Deadline(key, p["name"], due, adjust(due, p.get("holiday", "none"), is_holiday), p["ref"], short=p["name"])
