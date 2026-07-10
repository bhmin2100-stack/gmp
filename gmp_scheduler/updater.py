from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import __version__

try:
    from .build_info import BUILD_COMMIT, BUILD_DATE, BUILD_ID
except Exception:  # pragma: no cover - build metadata is optional in source runs.
    BUILD_COMMIT = "local"
    BUILD_DATE = ""
    BUILD_ID = "local"


RELEASE_API_URL = "https://api.github.com/repos/bhmin2100-stack/gmp/releases/tags/windows-latest"
EXE_ASSET_NAME = "GMP-Scheduler.exe"
VERSION_ASSET_NAME = "version.json"
USER_AGENT = f"GMP-Scheduler/{__version__}"


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    current_build_id: str
    current_commit: str
    latest_version: str
    latest_build_id: str
    latest_commit: str
    latest_build_date: str
    release_url: str
    exe_url: str
    sha256: str = ""
    size: int = 0

    @property
    def is_available(self) -> bool:
        version_cmp = compare_versions(self.latest_version, self.current_version)
        if version_cmp > 0:
            return True
        if version_cmp < 0:
            return False
        if self.current_build_id in ("", "local"):
            return False
        return bool(self.latest_build_id and self.latest_build_id != self.current_build_id)

    @property
    def current_label(self) -> str:
        if self.current_build_id and self.current_build_id != "local":
            return f"{self.current_version} ({self.current_build_id})"
        return self.current_version

    @property
    def latest_label(self) -> str:
        if self.latest_build_id:
            return f"{self.latest_version} ({self.latest_build_id})"
        return self.latest_version


def is_packaged_app() -> bool:
    return bool(getattr(sys, "frozen", False)) and Path(sys.executable).suffix.lower() == ".exe"


def current_version() -> str:
    return __version__


def current_build_id() -> str:
    return str(BUILD_ID or "local")


def compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    max_len = max(len(left_parts), len(right_parts), 1)
    left_parts += [0] * (max_len - len(left_parts))
    right_parts += [0] * (max_len - len(right_parts))
    if left_parts > right_parts:
        return 1
    if left_parts < right_parts:
        return -1
    return 0


def fetch_update_info(timeout: int = 10) -> UpdateInfo:
    release = _read_json(RELEASE_API_URL, timeout=timeout)
    assets = release.get("assets") or []
    exe_asset = _find_asset(assets, EXE_ASSET_NAME)
    if not exe_asset:
        raise RuntimeError(f"{EXE_ASSET_NAME} release asset not found.")

    metadata = {}
    version_asset = _find_asset(assets, VERSION_ASSET_NAME)
    if version_asset:
        try:
            metadata = _read_json(str(version_asset["browser_download_url"]), timeout=timeout)
        except Exception:
            metadata = {}

    latest_version = str(metadata.get("version") or release.get("tag_name") or "")
    latest_build_id = str(metadata.get("build_id") or release.get("target_commitish") or "")
    latest_commit = str(metadata.get("commit") or "")
    latest_build_date = str(metadata.get("build_date") or release.get("published_at") or "")
    sha256 = str(metadata.get("sha256") or "")
    size = int(metadata.get("size") or exe_asset.get("size") or 0)

    return UpdateInfo(
        current_version=current_version(),
        current_build_id=current_build_id(),
        current_commit=str(BUILD_COMMIT or ""),
        latest_version=latest_version,
        latest_build_id=latest_build_id,
        latest_commit=latest_commit,
        latest_build_date=latest_build_date,
        release_url=str(release.get("html_url") or ""),
        exe_url=str(exe_asset["browser_download_url"]),
        sha256=sha256,
        size=size,
    )


def download_update(
    info: UpdateInfo,
    progress: Optional[Callable[[int, int], None]] = None,
    timeout: int = 30,
) -> Path:
    update_dir = Path(tempfile.gettempdir()) / "gmp_scheduler_update"
    update_dir.mkdir(parents=True, exist_ok=True)
    target = update_dir / f"{_safe_name(info.latest_build_id or info.latest_version)}.new.exe"
    request = urllib.request.Request(info.exe_url, headers={"User-Agent": USER_AGENT})
    hasher = hashlib.sha256()
    downloaded = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        total = int(response.headers.get("Content-Length") or info.size or 0)
        with target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)
    if info.sha256:
        digest = hasher.hexdigest().lower()
        if digest != info.sha256.lower():
            target.unlink(missing_ok=True)
            raise RuntimeError("Downloaded update checksum did not match the release metadata.")
    return target


def launch_self_update(downloaded_exe: Path) -> None:
    if not is_packaged_app():
        raise RuntimeError("Self-update is only available from the packaged Windows EXE.")
    current_exe = Path(sys.executable).resolve()
    script = _write_update_script(current_exe=current_exe, downloaded_exe=downloaded_exe.resolve(), pid=os.getpid())
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        close_fds=True,
        creationflags=creationflags,
    )


def _read_json(url: str, timeout: int) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _find_asset(assets: list[dict], name: str) -> Optional[dict]:
    for asset in assets:
        if asset.get("name") == name:
            return asset
    return None


def _version_parts(value: str) -> list[int]:
    return [int(part) for part in re.findall(r"\d+", value)]


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return name or "GMP-Scheduler"


def _ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _write_update_script(current_exe: Path, downloaded_exe: Path, pid: int) -> Path:
    script = Path(tempfile.gettempdir()) / f"gmp_scheduler_update_{pid}.ps1"
    log_path = Path(tempfile.gettempdir()) / "gmp_scheduler_update.log"
    backup_exe = current_exe.with_suffix(current_exe.suffix + ".bak")
    script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$targetExe = {_ps_quote(current_exe)}
$newExe = {_ps_quote(downloaded_exe)}
$backupExe = {_ps_quote(backup_exe)}
$logPath = {_ps_quote(log_path)}
$pidToWait = {pid}

function Move-WithRetry($source, $destination) {{
    for ($i = 0; $i -lt 40; $i++) {{
        try {{
            Move-Item -LiteralPath $source -Destination $destination -Force
            return
        }} catch {{
            Start-Sleep -Milliseconds 500
        }}
    }}
    Move-Item -LiteralPath $source -Destination $destination -Force
}}

try {{
    Wait-Process -Id $pidToWait -Timeout 60 -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
    if (Test-Path -LiteralPath $backupExe) {{
        Remove-Item -LiteralPath $backupExe -Force -ErrorAction SilentlyContinue
    }}
    if (Test-Path -LiteralPath $targetExe) {{
        Move-WithRetry $targetExe $backupExe
    }}
    try {{
        Move-WithRetry $newExe $targetExe
    }} catch {{
        if ((Test-Path -LiteralPath $backupExe) -and -not (Test-Path -LiteralPath $targetExe)) {{
            Move-Item -LiteralPath $backupExe -Destination $targetExe -Force
        }}
        throw
    }}
    Start-Process -FilePath $targetExe -WorkingDirectory (Split-Path -Parent $targetExe)
    Start-Sleep -Seconds 2
    if (Test-Path -LiteralPath $backupExe) {{
        Remove-Item -LiteralPath $backupExe -Force -ErrorAction SilentlyContinue
    }}
}} catch {{
    $_ | Out-File -FilePath $logPath -Encoding UTF8
    exit 1
}} finally {{
    Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
}}
""".lstrip(),
        encoding="utf-8",
    )
    return script
