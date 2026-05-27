from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Dict, Set


SETTINGS_PATH = Path("calendar_overrides.json")


def _empty() -> Dict[str, list[str]]:
    return {
        "custom_holidays": [],
        "excluded_holidays": [],
        "custom_family_days": [],
        "excluded_family_days": [],
    }


def load_calendar_settings() -> Dict[str, Set[date]]:
    raw = _empty()
    if SETTINGS_PATH.exists():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw.update({k: loaded.get(k, []) for k in raw})
        except Exception:
            pass
    result: Dict[str, Set[date]] = {}
    for key, values in raw.items():
        dates: Set[date] = set()
        for value in values:
            try:
                dates.add(date.fromisoformat(str(value)))
            except ValueError:
                continue
        result[key] = dates
    return result


def save_calendar_settings(settings: Dict[str, Set[date]]) -> None:
    data = {key: sorted(d.isoformat() for d in values) for key, values in settings.items()}
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_custom_holiday(d: date) -> None:
    settings = load_calendar_settings()
    settings.setdefault("custom_holidays", set()).add(d)
    settings.setdefault("excluded_holidays", set()).discard(d)
    save_calendar_settings(settings)


def remove_holiday(d: date) -> None:
    settings = load_calendar_settings()
    settings.setdefault("custom_holidays", set()).discard(d)
    settings.setdefault("excluded_holidays", set()).add(d)
    save_calendar_settings(settings)


def add_custom_family_day(d: date) -> None:
    settings = load_calendar_settings()
    settings.setdefault("custom_family_days", set()).add(d)
    settings.setdefault("excluded_family_days", set()).discard(d)
    save_calendar_settings(settings)


def remove_family_day(d: date) -> None:
    settings = load_calendar_settings()
    settings.setdefault("custom_family_days", set()).discard(d)
    settings.setdefault("excluded_family_days", set()).add(d)
    save_calendar_settings(settings)
