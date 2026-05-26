from __future__ import annotations

from datetime import timedelta
from typing import List

from .calendar_utils import month_dates
from .models import OFF, SHIFT_GY, SHIFT_GY_REST, Employee, ScheduleMap


GY_BLOCK_DAYS = 6


def expand_gy_blocks(employees: List[Employee], year: int, month: int, schedule: ScheduleMap) -> None:
    """Expand a pasted G/지근 start marker into a 6-day work block in-place.

    Source Excel rosters mark weekday GY only on the first day. For this app's
    visible roster, validation, and statistics, that marker means the employee
    works six consecutive G/지근 days. Existing explicit non-empty work marks are
    not overwritten, but blanks and old 지휴 placeholders are filled.
    """
    valid_dates = set(month_dates(year, month))
    for emp in employees:
        starts = []
        for d in month_dates(year, month):
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
                if current in (OFF, SHIFT_GY_REST, ""):
                    schedule[d][emp.key] = SHIFT_GY
