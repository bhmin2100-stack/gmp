from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from gmp_scheduler.models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_SWING, Employee, ScheduleResult
from gmp_scheduler.monthly_summary import MONTHLY_SUMMARY_COLUMNS, employee_monthly_summary, monthly_summary_group_header
from gmp_scheduler.summary_layout_settings import load_summary_layout, merge_summary_groups, save_summary_layout, split_summary_group, summary_group_id


class MonthlySummaryTests(unittest.TestCase):
    def test_counts_day_swing_gy_and_saturday_duty(self) -> None:
        employee = Employee("홍길동", "1001")
        schedule = {
            date(2026, 7, day): {employee.key: OFF}
            for day in range(1, 32)
        }
        schedule[date(2026, 7, 1)][employee.key] = SHIFT_DAY       # Wednesday
        schedule[date(2026, 7, 2)][employee.key] = SHIFT_SWING     # Thursday
        schedule[date(2026, 7, 3)][employee.key] = SHIFT_GY        # Friday, still GY despite custom holiday
        schedule[date(2026, 7, 4)][employee.key] = SHIFT_DAY       # Saturday
        schedule[date(2026, 7, 5)][employee.key] = SHIFT_SWING     # Sunday
        schedule[date(2026, 7, 6)][employee.key] = SHIFT_GY        # Monday
        schedule[date(2026, 7, 11)][employee.key] = SHIFT_DUTY     # Saturday duty
        result = ScheduleResult(
            2026,
            7,
            [employee],
            schedule,
            {date(2026, 7, 3)},
        )

        self.assertEqual(
            employee_monthly_summary(result, employee),
            {
                "weekday_day": 1,
                "weekday_swing": 1,
                "gy": 2,
                "holiday_day": 1,
                "holiday_swing": 1,
                "saturday_duty": 1,
            },
        )

    def test_column_keys_are_unique(self) -> None:
        keys = [column.key for column in MONTHLY_SUMMARY_COLUMNS]
        self.assertEqual(len(keys), 6)
        self.assertEqual(len(keys), len(set(keys)))

    def test_groups_merge_sum_labels_and_split(self) -> None:
        groups = [(column.key,) for column in MONTHLY_SUMMARY_COLUMNS]
        merged = merge_summary_groups(groups, "weekday_day", "weekday_swing")
        self.assertEqual(merged[0], ("weekday_swing", "weekday_day"))
        self.assertEqual(monthly_summary_group_header(merged[0]), "합계\n평SW+평D")
        split = split_summary_group(merged, summary_group_id(merged[0]))
        self.assertEqual(split[:2], [("weekday_swing",), ("weekday_day",)])
        self.assertEqual(len(split), 6)

    def test_layout_round_trip_persists_groups_and_hidden_columns(self) -> None:
        groups = [
            ("weekday_day", "weekday_swing"),
            ("gy",),
            ("holiday_day", "holiday_swing", "saturday_duty"),
        ]
        with TemporaryDirectory() as temp:
            path = Path(temp) / "summary-layout.json"
            save_summary_layout(groups, {"gy"}, path)
            loaded_groups, hidden = load_summary_layout(path)
        self.assertEqual(loaded_groups, groups)
        self.assertEqual(hidden, {"gy"})

    def test_legacy_gy_layout_is_migrated(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "summary-layout.json"
            path.write_text(
                '{"groups":[["weekday_day","weekday_gy"],["holiday_gy"],["holiday_day"]],'
                '"hidden":["holiday_gy"]}',
                encoding="utf-8",
            )
            groups, hidden = load_summary_layout(path)
        self.assertIn(("weekday_day", "gy"), groups)
        self.assertNotIn(("holiday_gy",), groups)
        self.assertIn(("saturday_duty",), groups)
        self.assertEqual(hidden, {"gy"})


if __name__ == "__main__":
    unittest.main()
