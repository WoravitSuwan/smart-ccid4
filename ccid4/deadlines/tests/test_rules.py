"""Unit tests for deadlines/rules.py — every penalty band and the edge cases listed in SPEC A5."""

from datetime import UTC, date, datetime, timedelta

import pytest

from deadlines import rules
from deadlines.rules import DEFAULTS as R
from deadlines.rules import TZ, Deadline, Period

NO_HOLIDAYS = rules.holiday_checker([], weekends=False)
WEEKENDS = rules.holiday_checker([])


def bkk(*a):
    return datetime(*a, tzinfo=TZ)


def periods(start: date, *lengths):
    out, d = [], start
    for n in lengths:
        out.append(Period(d, rules.add_days(d, n)))
        d = out[-1].end + timedelta(days=1)
    return out


# ---------------------------------------------------------------- time helpers
def test_naive_datetime_is_refused():
    with pytest.raises(ValueError):
        rules.to_local(datetime(2026, 11, 1, 9, 0))


def test_local_date_uses_thai_time_not_utc():
    # 17:30 UTC on 14 Nov is 00:30 on 15 Nov in Thailand
    assert rules.local_date(datetime(2026, 11, 14, 17, 30, tzinfo=UTC)) == date(2026, 11, 15)
    assert rules.local_date(datetime(2026, 11, 14, 16, 59, tzinfo=UTC)) == date(2026, 11, 14)


def test_counting_includes_first_day():
    assert rules.add_days(date(2026, 1, 1), 12) == date(2026, 1, 12)
    assert rules.add_days(date(2026, 1, 1), 12, count_first_day=False) == date(2026, 1, 13)
    assert rules.period_days(date(2026, 1, 1), date(2026, 1, 12)) == 12


def test_period_crosses_month_and_year_end():
    assert rules.add_days(date(2026, 12, 25), 12) == date(2027, 1, 5)
    assert rules.add_days(date(2028, 2, 20), 12) == date(2028, 3, 2)  # leap year


def test_adjust_for_holidays():
    sat, sun, mon, fri = date(2026, 11, 21), date(2026, 11, 22), date(2026, 11, 23), date(2026, 11, 20)
    assert sat.weekday() == 5
    assert rules.adjust(sat, "before", WEEKENDS) == fri
    assert rules.adjust(sun, "after", WEEKENDS) == mon
    assert rules.adjust(sat, "none", WEEKENDS) == sat
    with_friday_off = rules.holiday_checker([fri])
    assert rules.adjust(sun, "before", with_friday_off) == date(2026, 11, 19)
    assert rules.adjust(sat, "before", NO_HOLIDAYS) == sat


def test_endless_holidays_are_refused():
    always = rules.holiday_checker([date(2026, 1, 1) + timedelta(days=i) for i in range(100)], weekends=False)
    with pytest.raises(ValueError):
        rules.adjust(date(2026, 1, 10), "after", always)


# ---------------------------------------------------------------- custody 48 hours
def test_custody_48_hours_crosses_midnight():
    dl = rules.custody_deadline(bkk(2026, 11, 14, 22, 30), R["custody"], party_id=7)
    assert dl.due == bkk(2026, 11, 16, 22, 30) and dl.act_by == dl.due and dl.party_id == 7
    assert dl.ref == "ป.วิ.อาญา ม.87"


def test_custody_from_utc_input_is_reported_in_thai_time():
    dl = rules.custody_deadline(datetime(2026, 11, 14, 17, 30, tzinfo=UTC), R["custody"])
    assert dl.due.tzinfo is TZ
    assert (dl.due.date(), dl.due.hour, dl.due.minute) == (date(2026, 11, 17), 0, 30)


def test_custody_is_not_moved_by_holidays():
    # arrives Friday evening: 48 hours end Sunday evening even though courts are closed
    dl = rules.custody_deadline(bkk(2026, 11, 20, 19, 0), R["custody"])
    assert dl.due == bkk(2026, 11, 22, 19, 0)


def test_file_charge_deadline_magistrate():
    dl = rules.file_deadline(bkk(2026, 11, 1, 23, 59), R["delay_magistrate"])
    assert dl.due == bkk(2026, 11, 3, 23, 59) and dl.key == "file_charge"


# ---------------------------------------------------------------- penalty bands
@pytest.mark.parametrize(
    ("months", "fine", "band"),
    [
        (None, None, None),
        (0, None, "remand_minor"),
        (1, 500, "remand_minor"),
        (6, None, "remand_minor"),
        (6, 500, "remand_minor"),
        (None, 500, "remand_minor"),
        (6, 501, "remand_mid"),
        (None, 501, "remand_mid"),
        (7, None, "remand_mid"),
        (60, 100_000, "remand_mid"),
        (119, None, "remand_mid"),
        (120, None, "remand_major"),
        (240, 400_000, "remand_major"),
    ],
)
def test_penalty_band(months, fine, band):
    assert rules.penalty_band(months, fine, R) == band


def test_penalty_band_follows_configured_values():
    custom = R | {"remand_minor": R["remand_minor"] | {"max_months": 3}}
    assert rules.penalty_band(4, None, custom) == "remand_mid"


# ---------------------------------------------------------------- remand plans
def test_minor_one_time_seven_days():
    p = R["remand_minor"]
    empty = rules.plan_periods("remand_minor", p, [], WEEKENDS)
    first = rules.next_period(empty, date(2026, 11, 2), p)
    assert first == Period(date(2026, 11, 2), date(2026, 11, 8))
    plan = rules.plan_periods("remand_minor", p, [first], NO_HOLIDAYS)
    assert (plan.used_days, plan.used_times, plan.remaining_days, plan.remaining_times) == (7, 1, 0, 0)
    assert plan.exhausted and plan.final_day == date(2026, 11, 8) and not plan.problems
    with pytest.raises(ValueError):
        rules.next_period(plan, date(2026, 11, 9), p)


def test_minor_second_time_or_too_long_is_flagged():
    p = R["remand_minor"]
    plan = rules.plan_periods("remand_minor", p, periods(date(2026, 11, 2), 3, 3), NO_HOLIDAYS)
    assert any("เกิน 1 ครั้ง" in x for x in plan.problems)
    plan = rules.plan_periods("remand_minor", p, periods(date(2026, 11, 2), 8), NO_HOLIDAYS)
    assert any("เกินครั้งละ 7 วัน" in x for x in plan.problems) and any("เกินที่กฎหมายให้ 7 วัน" in x for x in plan.problems)


def test_mid_five_years_acceptance_case():
    """เกณฑ์ตรวจรับข้อ 4: โทษสูงสุด 5 ปี → ฝากครั้งแรก → ผัดที่เหลือ และครบกำหนดรวม 48 วัน."""
    p = R[rules.penalty_band(60, None, R)]
    assert p["total_days"] == 48
    first = rules.next_period(rules.plan_periods("remand_mid", p, [], NO_HOLIDAYS), date(2026, 11, 10), p)
    assert first.end == date(2026, 11, 21)
    plan = rules.plan_periods("remand_mid", p, [first], NO_HOLIDAYS)
    assert (plan.used_days, plan.remaining_days, plan.remaining_times) == (12, 36, 3)
    assert plan.final_day == date(2026, 11, 10) + timedelta(days=47) == date(2026, 12, 27)


def test_mid_four_periods_use_all_48_days():
    p = R["remand_mid"]
    plan = rules.plan_periods("remand_mid", p, periods(date(2026, 11, 10), 12, 12, 12, 12), NO_HOLIDAYS)
    assert plan.used_days == 48 and plan.exhausted and not plan.problems
    assert plan.final_day == date(2026, 12, 27)
    over = rules.plan_periods("remand_mid", p, periods(date(2026, 11, 10), 12, 12, 12, 12, 1), NO_HOLIDAYS)
    assert any("เกินที่กฎหมายให้ 48 วัน" in x for x in over.problems)


def test_mid_short_periods_leave_a_partial_last_round():
    p = R["remand_mid"]
    plan = rules.plan_periods("remand_mid", p, periods(date(2026, 11, 10), 10, 12), NO_HOLIDAYS)
    assert (plan.remaining_days, plan.remaining_times) == (26, 3)
    nxt = rules.next_period(plan, plan.current_end + timedelta(days=1), p)
    assert nxt.days == 12
    plan = rules.plan_periods("remand_mid", p, periods(date(2026, 11, 10), 12, 12, 12, 10), NO_HOLIDAYS)
    assert (plan.remaining_days, plan.remaining_times) == (2, 1)
    assert rules.next_period(plan, plan.current_end + timedelta(days=1), p).days == 2


def test_major_84_days():
    p = R["remand_major"]
    plan = rules.plan_periods("remand_major", p, periods(date(2026, 1, 1), 12, 12), NO_HOLIDAYS)
    assert (plan.used_days, plan.remaining_days, plan.remaining_times) == (24, 60, 5)
    assert plan.final_day == date(2026, 1, 1) + timedelta(days=83)
    full = rules.plan_periods("remand_major", p, periods(date(2026, 1, 1), *[12] * 7), NO_HOLIDAYS)
    assert full.used_days == 84 and full.exhausted and not full.problems


def test_gap_and_overlap_are_flagged():
    p = R["remand_mid"]
    gap = [Period(date(2026, 11, 1), date(2026, 11, 12)), Period(date(2026, 11, 15), date(2026, 11, 26))]
    assert any("ไม่ต่อเนื่อง" in x for x in rules.plan_periods("remand_mid", p, gap, NO_HOLIDAYS).problems)
    overlap = [Period(date(2026, 11, 1), date(2026, 11, 12)), Period(date(2026, 11, 12), date(2026, 11, 23))]
    assert any("ทับ" in x for x in rules.plan_periods("remand_mid", p, overlap, NO_HOLIDAYS).problems)
    backwards = [Period(date(2026, 11, 12), date(2026, 11, 1))]
    assert any("ก่อนวันเริ่ม" in x for x in rules.plan_periods("remand_mid", p, backwards, NO_HOLIDAYS).problems)


def test_next_request_moves_before_a_court_holiday():
    p = R["remand_mid"]
    # period ends Saturday 21 Nov 2026 → ask for the next period by Friday 20 Nov
    plan = rules.plan_periods("remand_mid", p, [Period(date(2026, 11, 10), date(2026, 11, 21))], WEEKENDS)
    assert plan.current_end == date(2026, 11, 21) and plan.act_by == date(2026, 11, 20)
    # Friday is also a configured holiday → Thursday
    plan = rules.plan_periods(
        "remand_mid", p, [Period(date(2026, 11, 10), date(2026, 11, 21))], rules.holiday_checker([date(2026, 11, 20)])
    )
    assert plan.act_by == date(2026, 11, 19)


def test_magistrate_delay_six_days_five_times():
    p = R["delay_magistrate"]
    plan = rules.plan_periods("delay_magistrate", p, periods(date(2026, 11, 3), 6, 6), NO_HOLIDAYS)
    assert (plan.used_days, plan.remaining_times, plan.remaining_days) == (12, 3, 18)
    full = rules.plan_periods("delay_magistrate", p, periods(date(2026, 11, 3), 6, 6, 6, 6, 6), NO_HOLIDAYS)
    assert full.exhausted and not full.problems
    six = rules.plan_periods("delay_magistrate", p, periods(date(2026, 11, 3), 6, 6, 6, 6, 6, 1), NO_HOLIDAYS)
    assert any("เกิน 5 ครั้ง" in x for x in six.problems)
    long = rules.plan_periods("delay_magistrate", p, periods(date(2026, 11, 3), 7), NO_HOLIDAYS)
    assert any("เกินครั้งละ 6 วัน" in x for x in long.problems)


def test_max_times_limits_remaining_even_with_days_left():
    p = R["delay_magistrate"] | {"total_days": 40}
    plan = rules.plan_periods("delay_magistrate", p, periods(date(2026, 11, 3), 2, 2, 2, 2), NO_HOLIDAYS)
    assert (plan.remaining_times, plan.remaining_days) == (1, 6)


# ---------------------------------------------------------------- day-based duties
def test_progress_notices_30_and_60_days():
    start = bkk(2026, 11, 2, 10, 0)  # Monday
    one = rules.days_after(start, R["progress_1"], NO_HOLIDAYS, "progress_1")
    two = rules.days_after(start, R["progress_2"], NO_HOLIDAYS, "progress_2")
    assert (one.due, two.due) == (date(2026, 12, 2), date(2027, 1, 1))
    assert one.ref == "ตร.419"


def test_progress_notice_on_holiday_moves_to_next_working_day():
    two = rules.days_after(bkk(2026, 11, 2, 10, 0), R["progress_2"], rules.holiday_checker([date(2027, 1, 1)]), "progress_2")
    assert two.due == date(2027, 1, 1)
    assert two.act_by == date(2027, 1, 4)  # 1 Jan holiday, 2–3 Jan weekend


def test_day_counting_uses_thai_date_of_the_complaint():
    # 31 Oct 18:00 UTC = 1 Nov 01:00 in Thailand
    dl = rules.days_after(datetime(2026, 10, 31, 18, 0, tzinfo=UTC), R["progress_1"], NO_HOLIDAYS, "progress_1")
    assert dl.due == date(2026, 12, 1)


def test_unknown_offender_one_year_configurable():
    dl = rules.days_after(date(2026, 3, 1), R["unknown_offender"], NO_HOLIDAYS, "unknown_offender")
    assert dl.due == date(2027, 3, 1)
    dl = rules.days_after(date(2026, 3, 1), R["unknown_offender"] | {"days": 730}, NO_HOLIDAYS, "unknown_offender")
    assert dl.due == date(2028, 2, 29)


# ---------------------------------------------------------------- status colours
@pytest.mark.parametrize(
    ("due", "tone", "text"),
    [
        (date(2026, 11, 26), "primary", "เหลือ 10 วัน"),
        (date(2026, 11, 20), "primary", "เหลือ 4 วัน"),
        (date(2026, 11, 19), "warning", "เหลือ 3 วัน"),
        (date(2026, 11, 16), "warning", "ครบกำหนดวันนี้"),
        (date(2026, 11, 14), "danger", "เลยกำหนด 2 วัน"),
    ],
)
def test_status_for_dates(due, tone, text):
    st = rules.status(Deadline("x", "x", due, due), bkk(2026, 11, 16, 23, 59), warn_days=3)
    assert (st.tone, st.text) == (tone, text)


def test_status_for_hours_and_midnight():
    now = bkk(2026, 11, 16, 23, 0)
    soon = rules.status(Deadline("c", "c", bkk(2026, 11, 17, 4, 30), bkk(2026, 11, 17, 4, 30)), now)
    assert (soon.tone, soon.text) == ("warning", "เหลือ 5 ชม. 30 นาที")
    later = rules.status(Deadline("c", "c", bkk(2026, 11, 20, 1, 0), bkk(2026, 11, 20, 1, 0)), now)
    assert (later.tone, later.text) == ("primary", "เหลือ 3 วัน 2 ชม.")
    late = rules.status(Deadline("c", "c", bkk(2026, 11, 16, 20, 0), bkk(2026, 11, 16, 20, 0)), now)
    assert (late.tone, late.text) == ("danger", "เลยกำหนด 3 ชม.")
    # now given in UTC gives the same answer
    utc_now = now.astimezone(UTC)
    assert rules.status(Deadline("c", "c", bkk(2026, 11, 17, 4, 30), bkk(2026, 11, 17, 4, 30)), utc_now).text == soon.text


def test_sort_key_orders_dates_and_times_together():
    a = Deadline("a", "a", date(2026, 11, 17), date(2026, 11, 17))
    b = Deadline("b", "b", bkk(2026, 11, 17, 9, 0), bkk(2026, 11, 17, 9, 0))
    c = Deadline("c", "c", date(2026, 11, 16), date(2026, 11, 16))
    assert [d.key for d in sorted([a, b, c], key=Deadline.sort_key)] == ["c", "b", "a"]
