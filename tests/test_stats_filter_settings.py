from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gmp_scheduler.stats_filter_settings import ALL_FILTERS, load_monthly_stats_filter, save_monthly_stats_filter


class MonthlyStatsFilterSettingsTests(unittest.TestCase):
    def test_default_selects_every_filter_without_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pinned, filters = load_monthly_stats_filter(Path(temp) / "missing.json")
        self.assertFalse(pinned)
        self.assertEqual(filters, set(ALL_FILTERS))

    def test_pinned_filters_round_trip_including_empty_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "filters.json"
            selected = {("weekday", "day"), ("saturday", "gy")}
            save_monthly_stats_filter(selected, True, path)
            self.assertEqual(load_monthly_stats_filter(path), (True, selected))
            save_monthly_stats_filter(set(), True, path)
            self.assertEqual(load_monthly_stats_filter(path), (True, set()))

    def test_unpinned_setting_returns_default_filters_next_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "filters.json"
            save_monthly_stats_filter({("weekday", "day")}, False, path)
            pinned, filters = load_monthly_stats_filter(path)
        self.assertFalse(pinned)
        self.assertEqual(filters, set(ALL_FILTERS))

    def test_invalid_saved_filter_keys_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "filters.json"
            path.write_text(
                json.dumps({"pinned": True, "filters": [["weekday", "day"], ["bad", "bad"]]}),
                encoding="utf-8",
            )
            self.assertEqual(load_monthly_stats_filter(path), (True, {("weekday", "day")}))


if __name__ == "__main__":
    unittest.main()
