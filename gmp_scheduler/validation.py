from __future__ import annotations

from collections import Counter
from datetime import date
from typing import List, Set

from .calendar_utils import is_holiday_or_weekend, month_dates
from .models import OFF, SHIFT_SAT_DUTY, Employee, ScheduleMap, ShiftRules
from .stats import compute_stats


def validate_schedule(
    employees: List[Employee],
    year: int,
    month: int,
    schedule: ScheduleMap,
    holidays: Set[date],
    rules: ShiftRules,
) -> List[str]:
    warnings: List[str] = []
    employee_by_key = {e.key: e for e in employees}

    for d in month_dates(year, month):
        counts = Counter(shift for shift in schedule.get(d, {}).values() if shift and shift != OFF)
        min_rules = rules.min_holiday if is_holiday_or_weekend(d, holidays) else rules.min_weekday
        for shift, minimum in min_rules.items():
            if counts[shift] < minimum:
                warnings.append(f"{d.isoformat()} {shift} 최소 인원 부족: {counts[shift]}/{minimum}")
        if d.weekday() == 5 and counts[SHIFT_SAT_DUTY] < rules.saturday_duty_min:
            warnings.append(f"{d.isoformat()} 토당 최소 인원 부족: {counts[SHIFT_SAT_DUTY]}/{rules.saturday_duty_min}")

        for emp_key, shift in schedule.get(d, {}).items():
            emp = employee_by_key.get(emp_key)
            if not emp or not shift or shift == OFF:
                continue
            if d in emp.unavailable_dates:
                warnings.append(f"{d.isoformat()} {emp.name} 불가일 배정 위반: {shift}")

    stats = compute_stats(employees, month_dates(year, month), schedule, holidays)
    for s in stats.values():
        if s.max_consecutive_work > rules.max_consecutive_work_days:
            warnings.append(f"{s.name} 최대 연속근무 초과: {s.max_consecutive_work}/{rules.max_consecutive_work_days}")
        if s.max_consecutive_gy > rules.max_consecutive_gy:
            warnings.append(f"{s.name} 연속 GY 초과: {s.max_consecutive_gy}/{rules.max_consecutive_gy}")

    for emp in employees:
        if not emp.is_new:
            continue
        s = stats[emp.key]
        if (s.weekday_day + s.weekday_swing) == 0:
            warnings.append(f"신규 {emp.name} 평일 Day/SW 경험 없음")
        if (s.holiday_day + s.holiday_swing) == 0:
            warnings.append(f"신규 {emp.name} 휴일 Day/SW 경험 없음")
        if s.weekday_gy == 0:
            warnings.append(f"신규 {emp.name} 평일 GY 경험 없음")
    return warnings
