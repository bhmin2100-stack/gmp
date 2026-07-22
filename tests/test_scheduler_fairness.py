from __future__ import annotations

import unittest

from gmp_scheduler.models import (
    DAY_TYPE_ORDER,
    SHIFT_DAY,
    SHIFT_DUTY,
    SHIFT_GY,
    Employee,
    ShiftRules,
)
from gmp_scheduler.monthly_summary import employee_monthly_summary
from gmp_scheduler.scheduler import generate_month_schedule


class SchedulerFairnessTests(unittest.TestCase):
    def test_monthly_summary_columns_stay_balanced_before_other_scores(self) -> None:
        for employee_count in (8, 39):
            with self.subTest(employee_count=employee_count):
                employees = [Employee(f"직원{i}", str(i)) for i in range(employee_count)]
                result = generate_month_schedule(employees, 2026, 7, seed=7)
                summaries = [employee_monthly_summary(result, employee) for employee in employees]

                for key in (
                    "weekday_day",
                    "weekday_swing",
                    "holiday_day",
                    "holiday_swing",
                    "saturday_duty",
                ):
                    values = [summary[key] for summary in summaries]
                    self.assertLessEqual(max(values) - min(values), 1, (key, values))
                    self.assertLessEqual(len(set(values)), 2, (key, values))

    def test_history_breaks_ties_without_overloading_the_current_month(self) -> None:
        employees = [Employee("누적많음", "1"), Employee("누적적음A", "2"), Employee("누적적음B", "3")]
        day_only_rules = ShiftRules(
            min_by_day_type={
                day_type: {SHIFT_DAY: 1, SHIFT_GY: 0, SHIFT_DUTY: 0}
                for day_type in DAY_TYPE_ORDER
            },
            min_weekly_work_days=0,
        )
        history = {
            employees[0].key: {
                "weekday_day": 100,
                "holiday_day": 100,
                "total": 200,
            }
        }

        result = generate_month_schedule(
            employees,
            2026,
            7,
            rules=day_only_rules,
            seed=3,
            historical_summary_counts=history,
        )
        summaries = {employee.key: employee_monthly_summary(result, employee) for employee in employees}

        for key in ("weekday_day", "holiday_day"):
            values = [summaries[employee.key][key] for employee in employees]
            self.assertLessEqual(max(values) - min(values), 1, (key, values))
            self.assertEqual(summaries[employees[0].key][key], min(values), (key, values))


if __name__ == "__main__":
    unittest.main()
