from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .app_paths import app_data_file
from .models import ShiftRules

RULE_SETTINGS_PATH = app_data_file("rule_settings.json")
TEAM_RULE_KEYS = ("combined", "V11", "V12")


def rules_to_dict(rules: ShiftRules) -> Dict[str, object]:
    module_weights: Dict[str, Dict[str, int]] = {}
    for module_name, weights in rules.module_weights.items():
        if not module_name or not isinstance(weights, dict):
            continue
        cleaned: Dict[str, int] = {}
        for key, percent in weights.items():
            try:
                value = int(percent)
            except (TypeError, ValueError):
                continue
            if value > 0:
                cleaned[str(key)] = value
        if cleaned:
            module_weights[str(module_name)] = cleaned
    return {
        "min_weekday": dict(rules.min_weekday),
        "min_holiday": dict(rules.min_holiday),
        "min_by_day_type": {
            day_type: dict(values)
            for day_type, values in rules.min_by_day_type.items()
        },
        "max_consecutive_work_days": rules.max_consecutive_work_days,
        "max_consecutive_gy": rules.max_consecutive_gy,
        "min_weekly_work_days": rules.min_weekly_work_days,
        "allow_same_day_multiple_shift": rules.allow_same_day_multiple_shift,
        "module_weights": module_weights,
    }


def rules_from_dict(data: object) -> ShiftRules:
    rules = ShiftRules()
    if not isinstance(data, dict):
        return rules
    if isinstance(data.get("min_weekday"), dict):
        rules.min_weekday = {str(k): int(v) for k, v in data["min_weekday"].items()}
    if isinstance(data.get("min_holiday"), dict):
        rules.min_holiday = {str(k): int(v) for k, v in data["min_holiday"].items()}
    if isinstance(data.get("min_by_day_type"), dict):
        rules.min_by_day_type = {
            str(day_type): {str(k): int(v) for k, v in values.items()}
            for day_type, values in data["min_by_day_type"].items()
            if isinstance(values, dict)
        }
    for attr in ("max_consecutive_work_days", "max_consecutive_gy", "min_weekly_work_days"):
        value = data.get(attr)
        if isinstance(value, int):
            setattr(rules, attr, value)
    if isinstance(data.get("allow_same_day_multiple_shift"), bool):
        rules.allow_same_day_multiple_shift = data["allow_same_day_multiple_shift"]
    if isinstance(data.get("module_weights"), dict):
        module_weights: Dict[str, Dict[str, int]] = {}
        for module_name, weights in data["module_weights"].items():
            if not isinstance(weights, dict):
                continue
            cleaned: Dict[str, int] = {}
            for key, value in weights.items():
                try:
                    percent = int(value)
                except (TypeError, ValueError):
                    continue
                if percent > 0:
                    cleaned[str(key)] = percent
            if cleaned:
                module_weights[str(module_name)] = cleaned
        rules.module_weights = module_weights
    return rules


def load_team_rules(path: Path = RULE_SETTINGS_PATH) -> Dict[str, ShiftRules]:
    rules = {key: ShiftRules() for key in TEAM_RULE_KEYS}
    if not path.exists():
        return rules
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return rules
    if not isinstance(data, dict):
        return rules
    teams = data.get("teams", data)
    if not isinstance(teams, dict):
        return rules
    for key in TEAM_RULE_KEYS:
        rules[key] = rules_from_dict(teams.get(key))
    return rules


def save_team_rules(rules_by_team: Dict[str, ShiftRules], path: Path = RULE_SETTINGS_PATH) -> None:
    payload = {
        "teams": {
            key: rules_to_dict(rules_by_team.get(key, ShiftRules()))
            for key in TEAM_RULE_KEYS
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
