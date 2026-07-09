from __future__ import annotations

from datetime import date
from typing import Dict, Set

from .calendar_utils import is_duty_day, is_family_day
from .models import (
    DAY_TYPE_FAMILY,
    DAY_TYPE_PUBLIC_HOLIDAY,
    DAY_TYPE_SATURDAY,
    DAY_TYPE_SUNDAY,
    DAY_TYPE_WEEKDAY,
    SHIFT_DAY,
    SHIFT_DUTY,
    SHIFT_GY,
    SHIFT_SWING,
    ShiftRules,
)


def day_type_for_date(d: date, holidays: Set[date]) -> str:
    if is_family_day(d):
        return DAY_TYPE_FAMILY
    if is_duty_day(d):
        return DAY_TYPE_SATURDAY
    if d.weekday() == 6:
        return DAY_TYPE_SUNDAY
    if d in holidays:
        return DAY_TYPE_PUBLIC_HOLIDAY
    return DAY_TYPE_WEEKDAY


def stored_shift_for_day_type(day_type: str, shift: str) -> str:
    return SHIFT_DUTY if day_type == DAY_TYPE_SATURDAY and shift == SHIFT_GY else shift


def day_shift_key(day_type: str, shift: str) -> str:
    return f"{day_type}:{stored_shift_for_day_type(day_type, shift)}"


def day_shift_key_for_date(d: date, holidays: Set[date], shift: str) -> str:
    return f"{day_type_for_date(d, holidays)}:{shift}"


def min_rules_for_date(rules: ShiftRules, d: date, holidays: Set[date]) -> Dict[str, int]:
    day_type = day_type_for_date(d, holidays)
    configured = dict(rules.min_by_day_type.get(day_type, {}))
    if configured:
        return configured
    if day_type == DAY_TYPE_WEEKDAY:
        return dict(rules.min_weekday)
    if day_type == DAY_TYPE_SATURDAY:
        return dict(rules.min_holiday)
    return {
        SHIFT_DAY: rules.min_weekday.get(SHIFT_DAY, 1),
        SHIFT_SWING: rules.min_weekday.get(SHIFT_SWING, 1),
        SHIFT_GY: rules.min_weekday.get(SHIFT_GY, 1),
    }


def rule_value_for_display(rules: ShiftRules, day_type: str, shift: str) -> int:
    stored_shift = stored_shift_for_day_type(day_type, shift)
    return rules.min_by_day_type.get(day_type, {}).get(stored_shift, 0)


def set_rule_value_from_display(rules: ShiftRules, day_type: str, shift: str, value: int) -> None:
    stored_shift = stored_shift_for_day_type(day_type, shift)
    rules.min_by_day_type.setdefault(day_type, {})[stored_shift] = value
