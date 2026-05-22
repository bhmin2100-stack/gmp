from __future__ import annotations

import calendar
from datetime import date
from typing import List, Set


def month_dates(year: int, month: int) -> List[date]:
    last_day = calendar.monthrange(year, month)[1]
    return [date(year, month, day) for day in range(1, last_day + 1)]


def korean_holidays(year: int) -> Set[date]:
    """Return South Korea public holidays for a year.

    Uses the `holidays` package. If it is missing, falls back to weekends only
    via `is_holiday_or_weekend`, while returning an empty official holiday set.
    """
    try:
        import holidays  # type: ignore

        return {d for d in holidays.KR(years=[year]).keys()}
    except Exception:
        return set()


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_holiday_or_weekend(d: date, holiday_set: Set[date]) -> bool:
    return is_weekend(d) or d in holiday_set


def weekday_ko(d: date) -> str:
    return ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
