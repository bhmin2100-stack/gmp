from __future__ import annotations

import sys
from pathlib import Path


def bundled_resource(relative_path: str) -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root) / relative_path
    return Path(__file__).resolve().parents[1] / relative_path


def app_icon_path() -> Path:
    return bundled_resource("assets/gmp-scheduler.png")
