from __future__ import annotations

from datetime import timedelta
from typing import List

from .calendar_utils import month_dates
from .models import OFF, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, Employee, ScheduleMap


GY_BLOCK_DAYS = 6


def expand_gy_blocks(employees: List[Employee], year: int, month: int, schedule: ScheduleMap) -> None:
    """Expand a pasted G/지근 start marker into a 6-day work block in-place.

    Source Excel rosters mark weekday GY only on the first day. For this app's
    visible roster, validation, and statistics, that marker usually means the
    employee works a six-day G/지근 block. Existing pasted rosters can contain
    only the remaining part of a previous-month GY block, so expansion must stop
    when another employee's pasted G/지근/당직 appears. Existing explicit
    non-empty work marks are not overwritten.
    """
    dates = month_dates(year, month)
    valid_dates = set(dates)
    original_gy_or_duty_by_date = {
        d: {
            emp.key
            for emp in employees
            if schedule.get(d, {}).get(emp.key, OFF) in (SHIFT_GY, SHIFT_DUTY)
        }
        for d in dates
    }
    for emp in employees:
        starts = []
        for d in dates:
            if schedule.get(d, {}).get(emp.key, OFF) != SHIFT_GY:
                continue
            previous = d - timedelta(days=1)
            if previous in valid_dates and schedule.get(previous, {}).get(emp.key, OFF) == SHIFT_GY:
                continue
            starts.append(d)
        for start in starts:
            for offset in range(1, GY_BLOCK_DAYS):
                d = start + timedelta(days=offset)
                if d not in valid_dates:
                    continue
                current = schedule.setdefault(d, {}).get(emp.key, OFF)
                if any(other_key != emp.key for other_key in original_gy_or_duty_by_date.get(d, set())):
                    break
                if current not in (OFF, SHIFT_GY_REST, ""):
                    break
                if current in (OFF, SHIFT_GY_REST, ""):
                    schedule[d][emp.key] = SHIFT_GY
