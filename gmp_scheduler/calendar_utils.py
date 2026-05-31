from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import List, Set

from .calendar_settings import load_calendar_settings


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

        official = {d for d in holidays.KR(years=[year]).keys()}
    except Exception:
        official = set()
    settings = load_calendar_settings()
    custom = {d for d in settings.get("custom_holidays", set()) if d.year == year}
    excluded = {d for d in settings.get("excluded_holidays", set()) if d.year == year}
    return (official | custom | family_days(year)) - excluded


def default_family_days(year: int) -> Set[date]:
    """Friday of the week containing the 21st, for each month."""
    result: Set[date] = set()
    for month in range(1, 13):
        d = date(year, month, 21)
        friday = d + timedelta(days=4 - d.weekday())
        result.add(friday)
    return result


def family_days(year: int) -> Set[date]:
    settings = load_calendar_settings()
    custom = {d for d in settings.get("custom_family_days", set()) if d.year == year}
    excluded = {d for d in settings.get("excluded_family_days", set()) if d.year == year}
    return (default_family_days(year) | custom) - excluded


def is_family_day(d: date) -> bool:
    return d in family_days(d.year)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_duty_day(d: date) -> bool:
    """Saturday-only 당직 rule."""
    return d.weekday() == 5


def is_holiday_or_weekend(d: date, holiday_set: Set[date]) -> bool:
    return is_weekend(d) or d in holiday_set


def weekday_ko(d: date) -> str:
    return ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
