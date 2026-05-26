from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .calendar_utils import is_holiday_or_weekend, month_dates
from .models import OFF, SHIFT_DAY, SHIFT_DUTY, SHIFT_GY, SHIFT_GY_REST, SHIFT_SWING, Employee, ScheduleMap, ScheduleResult
from .schedule_utils import expand_gy_blocks
from .stats import EmployeeStats, compute_stats

DB_PATH = Path("gmp_scheduler.sqlite3")


SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    employee_no TEXT NOT NULL DEFAULT '',
    is_new INTEGER NOT NULL DEFAULT 0,
    UNIQUE(name, employee_no)
);

CREATE TABLE IF NOT EXISTS monthly_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    source_name TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL,
    UNIQUE(year, month, source_name)
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    employee_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    shift_code TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(schedule_id) REFERENCES monthly_schedules(id) ON DELETE CASCADE,
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE,
    UNIQUE(schedule_id, employee_id, work_date)
);

CREATE TABLE IF NOT EXISTS unavailable_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    source_name TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL,
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE,
    UNIQUE(employee_id, work_date, source_name)
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_employee(conn: sqlite3.Connection, emp: Employee) -> int:
    conn.execute(
        """
        INSERT INTO employees(name, employee_no, is_new)
        VALUES (?, ?, ?)
        ON CONFLICT(name, employee_no) DO UPDATE SET is_new=excluded.is_new
        """,
        (emp.name, emp.employee_id, 1 if emp.is_new else 0),
    )
    row = conn.execute(
        "SELECT id FROM employees WHERE name=? AND employee_no=?",
        (emp.name, emp.employee_id),
    ).fetchone()
    return int(row["id"])


def save_schedule(result: ScheduleResult, source_name: str = "") -> int:
    with connect() as conn:
        imported_at = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT INTO monthly_schedules(year, month, source_name, imported_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(year, month, source_name) DO UPDATE SET imported_at=excluded.imported_at
            """,
            (result.year, result.month, source_name, imported_at),
        )
        schedule_id = int(conn.execute(
            "SELECT id FROM monthly_schedules WHERE year=? AND month=? AND source_name=?",
            (result.year, result.month, source_name),
        ).fetchone()["id"])
        conn.execute("DELETE FROM assignments WHERE schedule_id=?", (schedule_id,))

        emp_ids = {emp.key: upsert_employee(conn, emp) for emp in result.employees}
        for d in month_dates(result.year, result.month):
            for emp in result.employees:
                conn.execute(
                    """
                    INSERT INTO assignments(schedule_id, employee_id, work_date, shift_code)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(schedule_id, employee_id, work_date)
                    DO UPDATE SET shift_code=excluded.shift_code
                    """,
                    (schedule_id, emp_ids[emp.key], d.isoformat(), result.schedule.get(d, {}).get(emp.key, OFF)),
                )
        return schedule_id


def save_unavailable_days(employees: List[Employee], source_name: str = "") -> int:
    count = 0
    with connect() as conn:
        imported_at = datetime.now().isoformat(timespec="seconds")
        for emp in employees:
            emp_id = upsert_employee(conn, emp)
            for d in emp.unavailable_dates:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO unavailable_days(employee_id, work_date, source_name, imported_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (emp_id, d.isoformat(), source_name, imported_at),
                )
                count += 1
    return count


def cumulative_stats() -> List[Dict[str, object]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT e.name, e.employee_no,
                   SUM(CASE WHEN a.shift_code=? THEN 1 ELSE 0 END) AS d_count,
                   SUM(CASE WHEN a.shift_code=? THEN 1 ELSE 0 END) AS s_count,
                   SUM(CASE WHEN a.shift_code=? THEN 1 ELSE 0 END) AS weekday_gy_count,
                   SUM(CASE WHEN a.shift_code=? THEN 1 ELSE 0 END) AS duty_count,
                   SUM(CASE WHEN a.shift_code=? THEN 1 ELSE 0 END) AS gy_rest_count,
                   SUM(CASE WHEN a.shift_code IN (?, ?, ?, ?) THEN 1 ELSE 0 END) AS total_work
            FROM employees e
            LEFT JOIN assignments a ON a.employee_id = e.id
            GROUP BY e.id
            ORDER BY e.name, e.employee_no
            """,
            (
                SHIFT_DAY,
                SHIFT_SWING,
                SHIFT_GY,
                SHIFT_DUTY,
                SHIFT_GY_REST,
                SHIFT_DAY,
                SHIFT_SWING,
                SHIFT_GY,
                SHIFT_DUTY,
            ),
        ).fetchall()
        return [dict(row) for row in rows]


def saved_months() -> List[Dict[str, object]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, year, month, source_name, imported_at
            FROM monthly_schedules
            ORDER BY year DESC, month DESC, source_name
            """
        ).fetchall()
        return [dict(row) for row in rows]


def load_schedule_result(year: int, month: int) -> Optional[ScheduleResult]:
    """Load the latest saved schedule for a year/month from the local DB."""
    from .calendar_utils import korean_holidays

    with connect() as conn:
        sched = conn.execute(
            """
            SELECT id, source_name
            FROM monthly_schedules
            WHERE year=? AND month=?
            ORDER BY imported_at DESC, id DESC
            LIMIT 1
            """,
            (year, month),
        ).fetchone()
        if not sched:
            return None
        rows = conn.execute(
            """
            SELECT e.name, e.employee_no, e.is_new, a.work_date, a.shift_code
            FROM assignments a
            JOIN employees e ON e.id = a.employee_id
            WHERE a.schedule_id=?
            ORDER BY e.name, e.employee_no, a.work_date
            """,
            (int(sched["id"]),),
        ).fetchall()
        if not rows:
            return None

        emp_by_key: Dict[str, Employee] = {}
        for row in rows:
            emp = Employee(str(row["name"]), str(row["employee_no"] or ""), bool(row["is_new"]))
            emp_by_key[emp.key] = emp
        employees = list(emp_by_key.values())
        schedule: ScheduleMap = {d: {emp.key: OFF for emp in employees} for d in month_dates(year, month)}
        for row in rows:
            d = date.fromisoformat(str(row["work_date"]))
            emp_key = f"{row['name']}|{row['employee_no'] or ''}"
            if d in schedule:
                schedule[d][emp_key] = str(row["shift_code"] or OFF)
        expand_gy_blocks(employees, year, month, schedule)
        return ScheduleResult(year, month, employees, schedule, korean_holidays(year))
