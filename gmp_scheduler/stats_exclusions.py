from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .app_paths import app_data_file


SETTINGS_PATH = app_data_file("stats_exclusions.json")


def _empty() -> Dict[str, Dict[str, str]]:
    return {
        "월간 통계": {},
        "근무율": {},
        "GY/당직": {},
    }


def load_stats_exclusions() -> Dict[str, Dict[str, str]]:
    data = _empty()
    if SETTINGS_PATH.exists():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for mode, people in loaded.items():
                    if isinstance(people, dict):
                        data.setdefault(str(mode), {}).update({str(k): str(v) for k, v in people.items()})
        except Exception:
            pass
    return data


def save_stats_exclusions(data: Dict[str, Dict[str, str]]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def excluded_people(mode: str) -> Dict[str, str]:
    return dict(load_stats_exclusions().get(mode, {}))


def exclude_person(mode: str, employee_key: str, label: str) -> None:
    data = load_stats_exclusions()
    data.setdefault(mode, {})[employee_key] = label
    save_stats_exclusions(data)


def include_person(mode: str, employee_key: str) -> None:
    data = load_stats_exclusions()
    data.setdefault(mode, {}).pop(employee_key, None)
    save_stats_exclusions(data)
