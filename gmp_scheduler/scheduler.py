from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .calendar_utils import is_holiday_or_weekend, korean_holidays, month_dates
from .models import OFF, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, Employee, ScheduleMap, ScheduleResult, ShiftRules
from .schedule_utils import expand_gy_blocks
from .validation import validate_schedule


class ScheduleError(RuntimeError):
    pass


def generate_month_schedule(
    employees: List[Employee],
    year: int,
    month: int,
    rules: Optional[ShiftRules] = None,
    seed: Optional[int] = None,
) -> ScheduleResult:
    if not employees:
        raise ScheduleError("직원이 없습니다.")
    rules = rules or ShiftRules()
    rng = random.Random(seed)
    dates = month_dates(year, month)
    holidays = korean_holidays(year)
    schedule: ScheduleMap = {d: {e.key: OFF for e in employees} for d in dates}
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def bucket(d: date, shift: str) -> str:
        prefix = "holiday" if is_holiday_or_weekend(d, holidays) else "weekday"
        if shift == SHIFT_DUTY:
            return "holiday_GY"
        return f"{prefix}_{shift}"

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

    def assign_one(d: date, shift: str) -> bool:
        candidates = [
            e for e in employees
            if schedule[d].get(e.key, OFF) == OFF and d not in e.unavailable_dates
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda e: candidate_score(e, d, shift))
        chosen = candidates[0]
        schedule[d][chosen.key] = shift
        counts[chosen.key][shift] += 1
        counts[chosen.key][bucket(d, shift)] += 1
        counts[chosen.key]["total"] += 1
        if is_holiday_or_weekend(d, holidays):
            counts[chosen.key]["holiday_work"] += 1
        return True

    for d in dates:
        min_rules = rules.min_holiday if is_holiday_or_weekend(d, holidays) else rules.min_weekday
        shift_order = list(min_rules.keys())
        rng.shuffle(shift_order)
        for shift in shift_order:
            for _ in range(min_rules.get(shift, 0)):
                assign_one(d, shift)

    expand_gy_blocks(employees, year, month, schedule)
    result = ScheduleResult(year=year, month=month, employees=employees, schedule=schedule, holidays=holidays)
    result.warnings = validate_schedule(employees, year, month, schedule, holidays, rules)
    return result
