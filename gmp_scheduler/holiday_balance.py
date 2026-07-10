from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Literal, Set

from .calendar_utils import is_holiday_or_weekend

HolidayRunPosition = Literal["edge", "middle"]


def holiday_run_positions(dates: Iterable[date], holidays: Set[date]) -> dict[date, HolidayRunPosition]:
    """Classify dates inside 3+ day holiday/weekend runs.

    Family days are already included in the holiday set, so a family-day Friday
    plus Saturday/Sunday becomes a three-day holiday run.
    """
    date_list = sorted(set(dates))
    if not date_list:
        return {}
    visible_dates = set(date_list)
    start = date_list[0] - timedelta(days=7)
    end = date_list[-1] + timedelta(days=7)
    positions: dict[date, HolidayRunPosition] = {}
    run: list[date] = []
    cur = start
    while cur <= end + timedelta(days=1):
        if cur <= end and is_holiday_or_weekend(cur, holidays):
            run.append(cur)
        else:
            if len(run) >= 3:
                for index, run_date in enumerate(run):
                    if run_date in visible_dates:
                        positions[run_date] = "edge" if index in (0, len(run) - 1) else "middle"
            run = []
        cur += timedelta(days=1)
    return positions
