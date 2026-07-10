from __future__ import annotations

from datetime import date
from typing import Iterable, Optional, Set

from .calendar_utils import is_holiday_or_weekend
from .models import SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_SWING, Employee, ScheduleMap

PAIR_WEEKDAY_DAY = "weekday_day"
PAIR_HOLIDAY_DAY = "holiday_day"
PAIR_GY = "gy"
PAIR_CATEGORY_ORDER = (PAIR_WEEKDAY_DAY, PAIR_HOLIDAY_DAY, PAIR_GY)
PAIR_CATEGORY_LABELS = {
    PAIR_WEEKDAY_DAY: "평일 D/S",
    PAIR_HOLIDAY_DAY: "주말/휴일 D/S",
    PAIR_GY: "GY/당직",
}


def pair_category(d: date, shift: str, holidays: Set[date]) -> Optional[str]:
    if shift in (SHIFT_DAY, SHIFT_SWING):
        return PAIR_HOLIDAY_DAY if is_holiday_or_weekend(d, holidays) else PAIR_WEEKDAY_DAY
    if shift in (SHIFT_GY, SHIFT_DUTY):
        return PAIR_GY
    return None


def has_pair_mentor(
    schedule: ScheduleMap,
    employees: Iterable[Employee],
    d: date,
    employee_key: str,
    shift: str,
) -> bool:
    for other in employees:
        if other.key == employee_key or other.pair_required:
            continue
        if schedule.get(d, {}).get(other.key) == shift:
            return True
    return False


def pair_coverage(
    schedule: ScheduleMap,
    employees: Iterable[Employee],
    emp: Employee,
    dates: Iterable[date],
    holidays: Set[date],
) -> set[str]:
    covered: set[str] = set()
    for d in dates:
        shift = schedule.get(d, {}).get(emp.key, "")
        category = pair_category(d, shift, holidays)
        if not category:
            continue
        if has_pair_mentor(schedule, employees, d, emp.key, shift):
            covered.add(category)
    return covered
