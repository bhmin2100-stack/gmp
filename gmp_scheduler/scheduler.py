from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

from .calendar_utils import is_duty_day, is_holiday_or_weekend, korean_holidays, month_dates
from .models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, SHIFT_SWING, Employee, ScheduleMap, ScheduleResult, ShiftRules
from .schedule_utils import GY_BLOCK_DAYS
from .validation import validate_schedule


class ScheduleError(RuntimeError):
    pass


def generate_month_schedule(
    employees: List[Employee],
    year: int,
    month: int,
    rules: Optional[ShiftRules] = None,
    seed: Optional[int] = None,
    previous_day_duty_employee_keys: Optional[Set[str]] = None,
) -> ScheduleResult:
    if not employees:
        raise ScheduleError("직원이 없습니다.")
    rules = rules or ShiftRules()
    rng = random.Random(seed)
    dates = month_dates(year, month)
    holidays = korean_holidays(year)
    schedule: ScheduleMap = {d: {e.key: OFF for e in employees} for d in dates}
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    previous_day_duty_employee_keys = previous_day_duty_employee_keys or set()
    week_dates: Dict[date, List[date]] = defaultdict(list)
    for d in dates:
        week_dates[d - timedelta(days=d.weekday())].append(d)

    def bucket(d: date, shift: str) -> str:
        prefix = "holiday" if is_holiday_or_weekend(d, holidays) else "weekday"
        if shift == SHIFT_DUTY:
            return "holiday_GY"
        return f"{prefix}_{shift}"

    def week_bucket(d: date) -> str:
        start = d - timedelta(days=d.weekday())
        return f"week_{start.isoformat()}"

    def weekly_work_target(emp: Employee, d: date) -> int:
        week_start = d - timedelta(days=d.weekday())
        available_days = sum(1 for cur in week_dates[week_start] if cur not in emp.unavailable_dates)
        return min(rules.min_weekly_work_days, available_days)

    def prev_shift(emp: Employee, d: date) -> str:
        return schedule.get(d - timedelta(days=1), {}).get(emp.key, OFF)

    def consecutive_work_before(emp: Employee, d: date) -> int:
        n = 0
        cur = d - timedelta(days=1)
        while cur in schedule and schedule[cur].get(emp.key, OFF) not in (OFF, SHIFT_GY_REST):
            n += 1
            cur -= timedelta(days=1)
        return n

    def consecutive_gy_before(emp: Employee, d: date) -> int:
        n = 0
        cur = d - timedelta(days=1)
        while cur in schedule and schedule[cur].get(emp.key, OFF) in (SHIFT_GY, SHIFT_DUTY):
            n += 1
            cur -= timedelta(days=1)
        return n

    def training_bonus(emp: Employee, d: date, shift: str) -> int:
        if not emp.is_new:
            return 0
        b = bucket(d, shift)
        if b in ("weekday_D", "weekday_S"):
            if counts[emp.key]["weekday_D"] + counts[emp.key]["weekday_S"] == 0:
                return -12
        if b in ("holiday_D", "holiday_S"):
            if counts[emp.key]["holiday_D"] + counts[emp.key]["holiday_S"] == 0:
                return -12
        if b == "weekday_G/지근" and counts[emp.key][b] == 0:
            return -14
        return 0

    def candidate_score(emp: Employee, d: date, shift: str) -> Tuple[float, float]:
        b = bucket(d, shift)
        score = 0.0
        score += counts[emp.key][b] * 16
        score += counts[emp.key][shift] * 4
        score += counts[emp.key]["total"] * 1.2
        weekly_count = counts[emp.key][week_bucket(d)]
        weekly_target = weekly_work_target(emp, d)
        if weekly_count < weekly_target:
            score -= (weekly_target - weekly_count) * 28
        else:
            score += (weekly_count - weekly_target + 1) * 14

        cw = consecutive_work_before(emp, d)
        cgy = consecutive_gy_before(emp, d)
        if cw >= rules.max_consecutive_work_days:
            score += 1000
        else:
            score += cw * 5
        if shift in (SHIFT_GY, SHIFT_DUTY):
            if cgy >= rules.max_consecutive_gy:
                score += 1000
            else:
                score += cgy * 40
        elif prev_shift(emp, d) in (SHIFT_GY, SHIFT_DUTY):
            score += 30

        if d in emp.unavailable_dates:
            score += 100000
        score += training_bonus(emp, d, shift)
        score += rng.random()
        return score, rng.random()

    def mark_assignment(emp: Employee, d: date, shift: str) -> None:
        schedule[d][emp.key] = shift
        counts[emp.key][shift] += 1
        counts[emp.key][bucket(d, shift)] += 1
        counts[emp.key][week_bucket(d)] += 1
        counts[emp.key]["total"] += 1
        if is_holiday_or_weekend(d, holidays):
            counts[emp.key]["holiday_work"] += 1

    def assign_one(d: date, shift: str) -> bool:
        candidates = [
            e for e in employees
            if schedule[d].get(e.key, OFF) == OFF and d not in e.unavailable_dates
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda e: candidate_score(e, d, shift))
        chosen = candidates[0]
        mark_assignment(chosen, d, shift)
        return True

    def has_gy_coverage(d: date) -> bool:
        return any(
            shift in (SHIFT_GY, SHIFT_DUTY)
            for shift in schedule.get(d, {}).values()
        )

    def can_start_gy_block(emp: Employee, start: date) -> bool:
        for d in gy_block_dates(start):
            if d in emp.unavailable_dates:
                return False
            if schedule[d].get(emp.key, OFF) != OFF:
                return False
        return True

    def gy_block_dates(start: date) -> List[date]:
        result: List[date] = []
        for offset in range(GY_BLOCK_DAYS):
            d = start + timedelta(days=offset)
            if d not in schedule:
                continue
            if is_duty_day(d):
                break
            result.append(d)
        return result

    def assign_gy_block_start(d: date) -> bool:
        blocked_keys = {
            emp.key
            for emp in employees
            if schedule.get(d - timedelta(days=1), {}).get(emp.key, OFF) == SHIFT_DUTY
        }
        if d == dates[0]:
            blocked_keys |= previous_day_duty_employee_keys
        candidates = [e for e in employees if e.key not in blocked_keys and can_start_gy_block(e, d)]
        if not candidates:
            return False
        candidates.sort(key=lambda e: candidate_score(e, d, SHIFT_GY))
        chosen = candidates[0]
        for cur in gy_block_dates(d):
            mark_assignment(chosen, cur, SHIFT_GY)
        return True

    for d in dates:
        min_rules = rules.min_holiday if is_duty_day(d) else rules.min_weekday
        if not is_duty_day(d) and not has_gy_coverage(d):
            assign_gy_block_start(d)
        day_swing_order = [shift for shift in (SHIFT_DAY, SHIFT_SWING) if shift in min_rules]
        for shift in day_swing_order:
            for _ in range(min_rules.get(shift, 0)):
                assign_one(d, shift)
        if not has_gy_coverage(d):
            gy_shift = SHIFT_DUTY if is_duty_day(d) else SHIFT_GY
            for _ in range(min_rules.get(gy_shift, 1)):
                assign_one(d, gy_shift)

    result = ScheduleResult(year=year, month=month, employees=employees, schedule=schedule, holidays=holidays)
    result.warnings = validate_schedule(employees, year, month, schedule, holidays, rules)
    return result
