from __future__ import annotations

import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from gmp_scheduler import __version__, build_info, updater


class UpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company = updater.COMPANY_CHANNEL
        self.release = {"tag_name": "0.2.11", "published_at": "2026-07-15T00:00:00Z", "body": "Release notes", "assets": [{"name": "GMP-Scheduler.exe", "browser_download_url": "https://company/exe", "size": 12}, {"name": "version.json", "browser_download_url": "https://company/meta"}]}

    def test_company_release_manager_manifest_is_parsed(self) -> None:
        manifest = {"appId": "gmp-scheduler", "name": "GMP Scheduler", "version": "0.2.11", "asset": "GMP-Scheduler.exe", "downloadUrl": "https://company/download/exe", "sha256": "abc", "notes": "Fixed scheduling", "publishedAtUtc": "2026-07-15T00:00:00Z"}
        info = updater._update_info_from_release(self.company, self.release, manifest, None)
        self.assertEqual(info.exe_url, manifest["downloadUrl"])
        self.assertEqual(info.notes, "Fixed scheduling")
        self.assertEqual(info.latest_build_date, manifest["publishedAtUtc"])
        self.assertFalse(info.build_id_updates)

    def test_legacy_manifest_is_compatible(self) -> None:
        manifest = {"version": "0.2.11", "build_id": "123-1", "commit": "abc", "build_date": "date", "exe_asset": "GMP-Scheduler.exe", "sha256": "def", "size": 99}
        info = updater._update_info_from_release(updater.PERSONAL_CHANNEL, self.release, manifest, None)
        self.assertEqual(info.latest_build_id, "123-1")
        self.assertEqual(info.exe_url, "https://company/exe")
        self.assertEqual(info.size, 99)

    def test_latest_api_response_loads_version_asset(self) -> None:
        def fake_read(url: str, timeout: int) -> dict:
            return self.release if url == self.company.release_api_url else {"version": "0.2.11", "downloadUrl": "https://company/exe"}
        with patch.object(updater, "_read_json", side_effect=fake_read):
            info = updater._fetch_update_info_from_api(self.company)
        self.assertEqual(info.latest_version, "0.2.11")
        self.assertEqual(info.exe_url, "https://company/exe")

    def test_company_release_requires_both_assets(self) -> None:
        release = {"assets": [{"name": "GMP-Scheduler.exe", "browser_download_url": "https://company/exe"}]}
        with patch.object(updater, "_read_json", return_value=release):
            with self.assertRaisesRegex(RuntimeError, "version.json"):
                updater._fetch_update_info_from_api(self.company)

    def test_company_same_version_does_not_repeat_update(self) -> None:
        info = updater.UpdateInfo("0.2.10", "old", "", "0.2.10", "new", "", "", "", "", channel="company")
        self.assertFalse(info.is_available)
        newer = updater.UpdateInfo("0.2.10", "old", "", "0.2.11", "", "", "", "", "", channel="company")
        self.assertTrue(newer.is_available)

    def test_build_channel_is_embedded_not_read_from_environment(self) -> None:
        self.assertEqual(build_info.APP_VERSION, __version__)
        self.assertEqual(build_info.UPDATE_CHANNEL, "personal")
        with patch.object(updater, "UPDATE_CHANNEL", "company"):
            self.assertEqual(updater.selected_channel(), updater.COMPANY_CHANNEL)
        with patch.object(updater, "UPDATE_CHANNEL", "personal"):
            self.assertEqual(updater.selected_channel(), updater.PERSONAL_CHANNEL)

    def test_company_build_pins_legacy_updater_compatible_pyinstaller(self) -> None:
        root = Path(__file__).resolve().parents[1]
        requirements = (root / "requirements-company-build.txt").read_text(encoding="utf-8")
        powershell = (root / "build-company-release.ps1").read_text(encoding="utf-8-sig")
        batch = (root / "MAKE_COMPANY_EXE.bat").read_text(encoding="utf-8")
        downloader = (root / "download-company-build.ps1").read_text(encoding="utf-8-sig")
        workflow = (root / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
        release_notes = (root / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertIn("pyinstaller==6.8.0", requirements.lower())
        self.assertIn('$requiredPyInstallerVersion = "6.8.0"', powershell)
        self.assertIn("PyInstaller.__version__ == '6.8.0'", batch)
        self.assertIn("download-company-build.ps1", batch)
        self.assertNotIn("winget install", batch.lower())
        self.assertIn('update_channel -ne "company"', downloader)
        self.assertIn("Get-FileHash", downloader)
        self.assertIn('Join-Path $root ".git"', downloader)
        self.assertIn('refs/remotes/origin/company-build', downloader)
        self.assertIn("GitHubDesktop", downloader)
        self.assertIn("archive --format=zip", downloader)
        self.assertIn("GMP-Scheduler-company.exe", workflow)
        self.assertIn("company-build.json", workflow)
        self.assertIn("HEAD:company-build", workflow)
        self.assertTrue(release_notes.startswith(f"# {__version__}\n"))
        self.assertIn("RELEASE_NOTES.md must start", workflow)
        self.assertIn("RELEASE-NOTES.txt", powershell)
        self.assertIn("RELEASE-NOTES.txt", batch)

    def test_version_comparison_and_notes_fallback(self) -> None:
        self.assertGreater(updater.compare_versions("1.10.0", "1.9.9"), 0)
        self.assertEqual(updater.compare_versions("1.0", "1.0.0"), 0)
        self.assertLess(updater.compare_versions("1.0.0", "1.0.1"), 0)
        info = updater._update_info_from_release(self.company, self.release, {"version": "0.2.11"}, None)
        self.assertEqual(info.notes, "Release notes")

    def test_update_prompt_displays_versions_and_notes(self) -> None:
        info = updater._update_info_from_release(
            self.company,
            self.release,
            {"version": "0.2.11", "notes": "공평 배정 개선"},
            None,
        )
        prompt = updater.update_prompt_text(info)
        self.assertIn(info.current_label, prompt)
        self.assertIn(info.latest_label, prompt)
        self.assertIn("변경 내용", prompt)
        self.assertIn("공평 배정 개선", prompt)

    def test_update_prompt_always_has_notes_section_and_missing_notes_block_update(self) -> None:
        info = updater.UpdateInfo("0.2.19", "", "", "0.2.20", "", "", "", "", "https://example/exe")
        prompt = updater.update_prompt_text(info)
        self.assertIn("변경 내용:", prompt)
        self.assertIn("등록되지 않았습니다", prompt)
        self.assertFalse(updater.has_release_notes(info))
        self.assertTrue(updater.has_release_notes(info.__class__(**{**info.__dict__, "notes": "필터 고정"})))

    def test_update_prompt_accumulates_only_versions_after_current(self) -> None:
        notes = (
            "# 0.2.21\n\n- 누적 변경 내용\n\n"
            "# 0.2.20\n\n- 필터 고정\n\n"
            "# 0.2.19\n\n- 팀별 경고\n\n"
            "# 0.2.18\n\n- 이미 설치됨"
        )
        info = updater.UpdateInfo(
            "0.2.18",
            "",
            "",
            "0.2.21",
            "",
            "",
            "",
            "",
            "https://example/exe",
            notes=notes,
        )
        prompt = updater.update_prompt_text(info)
        self.assertIn("[0.2.21]", prompt)
        self.assertIn("[0.2.20]", prompt)
        self.assertIn("[0.2.19]", prompt)
        self.assertNotIn("[0.2.18]", prompt)
        self.assertNotIn("이미 설치됨", prompt)

    def test_update_prompt_shows_only_latest_section_for_previous_version(self) -> None:
        notes = "# 0.2.21\n\n- 최신 기능\n\n# 0.2.20\n\n- 적용된 기능"
        info = updater.UpdateInfo(
            "0.2.20",
            "",
            "",
            "0.2.21",
            "",
            "",
            "",
            "",
            "https://example/exe",
            notes=notes,
        )
        self.assertEqual(updater.cumulative_update_notes(info), "[0.2.21]\n- 최신 기능")

    def test_sha256_mismatch_removes_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "update"
            info = updater.UpdateInfo("0", "", "", "1", "", "", "", "", "https://invalid", sha256="not-a-hash")
            with patch.object(updater, "update_work_dir", return_value=target), patch.object(updater.urllib.request, "urlopen") as urlopen:
                response = urlopen.return_value.__enter__.return_value
                response.headers.get.return_value = "3"
                response.read.side_effect = [b"abc", b""]
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    updater.download_update(info)
            self.assertFalse(list(target.glob("*.new.exe")))

    def test_401_and_403_are_clear_authentication_errors(self) -> None:
        for status in (401, 403):
            error = urllib.error.HTTPError("https://company", status, "Denied", {}, None)
            with patch.object(updater.urllib.request, "urlopen", side_effect=error):
                with self.assertRaises(updater.UpdateAuthenticationError) as raised:
                    updater._read_url_bytes("https://company", 1)
            self.assertIn("인증", str(raised.exception))

    def test_update_script_only_replaces_executable_not_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.object(updater, "update_work_dir", return_value=Path(temp) / "work"):
            root = Path(temp)
            database = root / "gmp_scheduler.sqlite3"
            database.write_bytes(b"database")
            script = updater._write_update_script(root / "GMP-Scheduler.exe", root / "new.exe", 42)
            self.assertTrue(database.exists())
            script_text = script.read_text(encoding="utf-8-sig")
            self.assertNotIn(str(database), script_text)
            self.assertIn("Where-Object { $_.Name -like '_PYI_*' }", script_text)
            self.assertIn("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'", script_text)
            self.assertIn("SetDllDirectory($null)", script_text)
            self.assertLess(
                script_text.index("PYINSTALLER_RESET_ENVIRONMENT"),
                script_text.index("Start-Process -FilePath $targetExe"),
            )

    def test_update_subprocess_environment_discards_pyinstaller_runtime_paths(self) -> None:
        with patch.dict(
            os.environ,
            {"_PYI_APPLICATION_HOME_DIR": "C:/missing-mei", "_PYI_ARCHIVE_FILE": "old.exe", "KEEP_ME": "yes"},
            clear=True,
        ):
            environment = updater._clean_update_environment()
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", environment)
        self.assertNotIn("_PYI_ARCHIVE_FILE", environment)
        self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(environment["KEEP_ME"], "yes")

    def test_launch_self_update_resets_dll_path_before_starting_powershell(self) -> None:
        script = Path("C:/Temp/gmp-update.ps1")
        calls: list[str] = []
        with patch.object(updater, "is_packaged_app", return_value=True), patch.object(
            updater,
            "update_install_error",
            return_value="",
        ), patch.object(updater, "_write_update_script", return_value=script), patch.object(
            updater,
            "_reset_windows_dll_search_path",
            side_effect=lambda: calls.append("reset_dll"),
        ) as reset_dll, patch.object(
            updater.subprocess,
            "Popen",
            side_effect=lambda *args, **kwargs: calls.append("popen"),
        ) as popen:
            updater.launch_self_update(Path("C:/Temp/new.exe"))
        reset_dll.assert_called_once_with()
        self.assertEqual(calls, ["reset_dll", "popen"])
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertFalse(any(key.upper().startswith("_PYI_") for key in environment))

    def test_unwritable_install_folder_has_clear_message(self) -> None:
        with patch.object(updater, "is_packaged_app", return_value=True), patch.object(
            Path,
            "open",
            side_effect=PermissionError("denied"),
        ):
            message = updater.update_install_error(Path("C:/Program Files/GMP/GMP-Scheduler.exe"))
        self.assertIn("쓸 권한", message)
        self.assertIn("사용자 쓰기 가능 폴더", message)

    def test_source_run_cannot_launch_exe_replacement(self) -> None:
        with patch.object(updater, "is_packaged_app", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "소스 실행"):
                updater.launch_self_update(Path("update.exe"))


if __name__ == "__main__":
    unittest.main()
