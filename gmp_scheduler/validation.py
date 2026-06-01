from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import List, Set

from .calendar_utils import is_duty_day, is_holiday_or_weekend, month_dates
from .models import OFF, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, Employee, ScheduleMap, ShiftRules
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
        counts = Counter(
            shift for shift in schedule.get(d, {}).values()
            if shift and shift not in (OFF, SHIFT_GY_REST)
        )
        min_rules = rules.min_holiday if is_duty_day(d) else rules.min_weekday
        for shift, minimum in min_rules.items():
            actual = counts[shift]
            if actual < minimum:
                warnings.append(f"{d.isoformat()} {shift} 최소 인원 부족: {actual}/{minimum}")
        if counts[SHIFT_DUTY] and counts[SHIFT_GY]:
            warnings.append(f"{d.isoformat()} 당직일에는 G/지근이 같이 있을 수 없음")

        for emp_key, shift in schedule.get(d, {}).items():
            emp = employee_by_key.get(emp_key)
            if not emp or not shift or shift in (OFF, SHIFT_GY_REST):
                continue
            if d in emp.unavailable_dates:
                warnings.append(f"{d.isoformat()} {emp.name} 불가일 배정 위반: {shift}")
            if shift == SHIFT_DUTY and not is_duty_day(d):
                warnings.append(f"{d.isoformat()} {emp.name} 당직은 토요일만 배정 가능함")
            if shift == SHIFT_GY and is_duty_day(d):
                warnings.append(f"{d.isoformat()} {emp.name} 토요일 GY는 당직으로 입력해야 함")
            prev_day = d - timedelta(days=1)
            if shift == SHIFT_GY and schedule.get(prev_day, {}).get(emp_key, OFF) == SHIFT_DUTY:
                warnings.append(f"{d.isoformat()} {emp.name} 당직 다음날 같은 사람이 G/지근 시작됨")

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
            warnings.append(f"신규 {emp.name} 평일 D/S 경험 없음")
        if (s.holiday_day + s.holiday_swing) == 0:
            warnings.append(f"신규 {emp.name} 휴일 D/S 경험 없음")
        if s.weekday_gy == 0:
            warnings.append(f"신규 {emp.name} 평일 G/지근 경험 없음")
    return warnings
