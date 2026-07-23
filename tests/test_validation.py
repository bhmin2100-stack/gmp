from __future__ import annotations

import unittest
from datetime import date, timedelta

from gmp_scheduler.calendar_utils import month_dates
from gmp_scheduler.models import (
    DAY_TYPE_ORDER,
    OFF,
    SHIFT_DAY,
    SHIFT_DUTY,
    SHIFT_GY,
    SHIFT_SWING,
    Employee,
    ScheduleResult,
    ShiftRules,
)
from gmp_scheduler.validation import format_warning_sections, validate_schedule, validate_schedules_by_source


class ValidationRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.employee = Employee("테스트", "1")
        self.dates = month_dates(2026, 7)
        self.schedule = {d: {self.employee.key: OFF} for d in self.dates}
        self.rules = ShiftRules(
            min_by_day_type={
                day_type: {SHIFT_DAY: 0, SHIFT_SWING: 0, SHIFT_GY: 0, SHIFT_DUTY: 0}
                for day_type in DAY_TYPE_ORDER
            },
            max_consecutive_work_days=5,
            max_consecutive_gy=6,
            min_weekly_work_days=0,
        )

    def warnings(self) -> list[str]:
        return validate_schedule(
            [self.employee],
            2026,
            7,
            self.schedule,
            set(),
            self.rules,
        )

    def test_six_day_gy_block_uses_gy_limit_not_day_swing_limit(self) -> None:
        start = date(2026, 7, 5)
        for offset in range(6):
            self.schedule[start + timedelta(days=offset)][self.employee.key] = SHIFT_GY
        warnings = self.warnings()
        self.assertFalse(any("Day/SW" in warning for warning in warnings), warnings)
        self.assertFalse(any("연속 GY 초과" in warning for warning in warnings), warnings)

    def test_day_swing_and_gy_limits_are_reported_independently(self) -> None:
        start = date(2026, 7, 13)
        for offset in range(6):
            self.schedule[start + timedelta(days=offset)][self.employee.key] = SHIFT_DAY
        warnings = self.warnings()
        self.assertTrue(any("최대 연속 Day/SW 초과: 6/5" in warning for warning in warnings), warnings)

        self.schedule = {d: {self.employee.key: OFF} for d in self.dates}
        gy_start = date(2026, 7, 5)
        for offset in range(6):
            self.schedule[gy_start + timedelta(days=offset)][self.employee.key] = SHIFT_GY
        self.schedule[date(2026, 7, 11)][self.employee.key] = SHIFT_DUTY
        warnings = self.warnings()
        self.assertTrue(any("연속 GY 초과: 7/6" in warning for warning in warnings), warnings)
        self.assertFalse(any("Day/SW" in warning for warning in warnings), warnings)

    def test_minimum_staff_warnings_follow_configured_shifts_and_values(self) -> None:
        self.rules.min_by_day_type = {
            day_type: {SHIFT_DAY: 0, SHIFT_SWING: 0, SHIFT_GY: 0, SHIFT_DUTY: 0}
            for day_type in DAY_TYPE_ORDER
        }
        self.rules.min_by_day_type[DAY_TYPE_ORDER[0]][SHIFT_DAY] = 2
        self.schedule[date(2026, 7, 1)][self.employee.key] = SHIFT_DAY
        minimum_warnings = [warning for warning in self.warnings() if "최소 인원 부족" in warning]
        self.assertTrue(minimum_warnings)
        self.assertTrue(all(" D 최소 인원 부족" in warning for warning in minimum_warnings))
        self.assertTrue(any("1/2" in warning for warning in minimum_warnings))

    def test_v11_and_v12_warnings_use_their_own_rules(self) -> None:
        zero_minimums = {
            day_type: {SHIFT_DAY: 0, SHIFT_SWING: 0, SHIFT_GY: 0, SHIFT_DUTY: 0}
            for day_type in DAY_TYPE_ORDER
        }
        v11_rules = ShiftRules(
            min_by_day_type={day_type: dict(values) for day_type, values in zero_minimums.items()},
            min_weekly_work_days=0,
        )
        v12_rules = ShiftRules(
            min_by_day_type={day_type: dict(values) for day_type, values in zero_minimums.items()},
            min_weekly_work_days=0,
        )
        v11_rules.min_by_day_type[DAY_TYPE_ORDER[0]][SHIFT_DAY] = 2
        v12_rules.min_by_day_type[DAY_TYPE_ORDER[0]][SHIFT_SWING] = 3
        self.schedule[date(2026, 7, 1)][self.employee.key] = SHIFT_DAY
        results = {
            source: ScheduleResult(2026, 7, [self.employee], self.schedule, set(), source_name=source)
            for source in ("V11", "V12")
        }

        grouped = validate_schedules_by_source(
            results,
            {"V11": v11_rules, "V12": v12_rules},
            ("V11", "V12"),
        )

        self.assertTrue(any(" D 최소 인원 부족: 1/2" in warning for warning in grouped["V11"]))
        self.assertFalse(any(" S 최소 인원 부족" in warning for warning in grouped["V11"]))
        self.assertTrue(any(" S 최소 인원 부족: 0/3" in warning for warning in grouped["V12"]))
        self.assertFalse(any(" D 최소 인원 부족" in warning for warning in grouped["V12"]))

    def test_grouped_warning_text_separates_v11_and_v12(self) -> None:
        text = format_warning_sections(
            {"V11": ["V11 부족"], "V12": ["V12 부족"]},
            ("V11", "V12"),
        )
        self.assertEqual(text, "[V11]\nV11 부족\n\n[V12]\nV12 부족")


if __name__ == "__main__":
    unittest.main()
