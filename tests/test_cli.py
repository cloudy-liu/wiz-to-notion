from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
from unittest import mock


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_environ = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_environ)

    def _missing_dotenv_path(self) -> Path:
        return Path(tempfile.gettempdir()) / "wiz-to-notion-tests-missing.env"

    def test_load_dotenv_sets_missing_values(self) -> None:
        from wiz_to_notion.cli import load_dotenv

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("NOTION_TOKEN=secret\nWIZ_TO_NOTION_SOURCE_DIR=D:\\notes\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                load_dotenv(env_path)
                self.assertEqual("secret", os.environ["NOTION_TOKEN"])
                self.assertEqual("D:\\notes", os.environ["WIZ_TO_NOTION_SOURCE_DIR"])

    def test_load_dotenv_uses_frozen_executable_directory_when_cwd_has_no_env(self) -> None:
        import wiz_to_notion.cli as cli

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            work_dir = root / "work"
            exe_dir = root / "release"
            work_dir.mkdir()
            exe_dir.mkdir()
            (exe_dir / ".env").write_text("NOTION_TOKEN=secret-from-exe\n", encoding="utf-8")
            executable = exe_dir / "wiz-to-notion.exe"
            executable.write_text("", encoding="utf-8")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(cli.Path, "cwd", return_value=work_dir),
                mock.patch.object(cli.sys, "executable", str(executable)),
                mock.patch.object(cli.sys, "frozen", True, create=True),
            ):
                cli.load_dotenv()
                self.assertEqual("secret-from-exe", os.environ["NOTION_TOKEN"])

    def test_package_entrypoint_can_run_as_script(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, str(repo_root / "src" / "wiz_to_notion" / "__main__.py"), "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("wiz-to-notion", completed.stdout)

    def test_scan_command_reports_discovered_markdown_without_token(self) -> None:
        from wiz_to_notion.cli import main

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            (source / "A").mkdir()
            (source / "A" / "One.md").write_text("# One", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch("wiz_to_notion.cli._default_dotenv_path", return_value=self._missing_dotenv_path()):
                exit_code = main(["scan", "--source", str(source)], stdout=stdout)

        self.assertEqual(0, exit_code)
        self.assertIn('"total_notes": 1', stdout.getvalue())

    def test_scan_requires_source_when_not_configured(self) -> None:
        from wiz_to_notion.cli import main

        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("sys.stderr", stderr),
            mock.patch("wiz_to_notion.cli._default_dotenv_path", return_value=self._missing_dotenv_path()),
            self.assertRaises(SystemExit) as exc_info,
        ):
            main(["scan"], stdout=io.StringIO())

        self.assertEqual(2, exc_info.exception.code)
        self.assertIn("missing source directory", stderr.getvalue())
        self.assertIn("WIZ_TO_NOTION_SOURCE_DIR", stderr.getvalue())

    def test_import_dry_run_uses_source_and_parent_from_env(self) -> None:
        from wiz_to_notion.cli import main

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            (source / "One.md").write_text("# One", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {
                    "WIZ_TO_NOTION_SOURCE_DIR": str(source),
                    "NOTION_PARENT_PAGE_ID": "https://www.notion.so/workspace/Import-Target-11111111222233334444555555555555",
                },
                clear=True,
            ), mock.patch("wiz_to_notion.cli._default_dotenv_path", return_value=self._missing_dotenv_path()):
                exit_code = main(["import", "--dry-run"], stdout=stdout, stderr=stderr)

        self.assertEqual(0, exit_code)
        output = stdout.getvalue()
        self.assertIn('"dry_run": true', output)
        self.assertIn('"total_notes": 1', output)
        self.assertIn("11111111-2222-3333-4444-555555555555", output)
        summary = stderr.getvalue()
        self.assertIn("Dry run complete.", summary)
        self.assertIn("Would import: 1", summary)
        self.assertIn("Skipped unchanged: 0", summary)
        self.assertIn("Failed: 0", summary)

    def test_import_dry_run_requires_source_when_not_configured(self) -> None:
        from wiz_to_notion.cli import main

        stderr = io.StringIO()
        with (
            mock.patch.dict(
                os.environ,
                {"NOTION_PARENT_PAGE_ID": "11111111222233334444555555555555"},
                clear=True,
            ),
            mock.patch("sys.stderr", stderr),
            mock.patch("wiz_to_notion.cli._default_dotenv_path", return_value=self._missing_dotenv_path()),
            self.assertRaises(SystemExit) as exc_info,
        ):
            main(["import", "--dry-run"], stdout=io.StringIO())

        self.assertEqual(2, exc_info.exception.code)
        self.assertIn("missing source directory", stderr.getvalue())
        self.assertIn("WIZ_TO_NOTION_SOURCE_DIR", stderr.getvalue())

    def test_import_dry_run_requires_parent_when_not_configured(self) -> None:
        from wiz_to_notion.cli import main

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            (source / "One.md").write_text("# One", encoding="utf-8")
            stderr = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"WIZ_TO_NOTION_SOURCE_DIR": str(source)}, clear=True),
                mock.patch("sys.stderr", stderr),
                mock.patch("wiz_to_notion.cli._default_dotenv_path", return_value=self._missing_dotenv_path()),
                self.assertRaises(SystemExit) as exc_info,
            ):
                main(["import", "--dry-run"], stdout=io.StringIO())

        self.assertEqual(2, exc_info.exception.code)
        self.assertIn("missing Notion parent page", stderr.getvalue())
        self.assertIn("NOTION_PARENT_PAGE_ID", stderr.getvalue())

    def test_import_dry_run_summary_reports_skipped_and_failed_counts(self) -> None:
        from wiz_to_notion.cli import main

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "One.md"
            note.write_text("# One", encoding="utf-8")
            state_dir = source / "_wiz"
            state_dir.mkdir(parents=True)
            state = state_dir / "state.json"
            state.write_text(
                '{"version": 1, "folders": {}, "notes": {"One.md": {"page_id": "page-1", "fingerprint": "x", "renderer_version": 5, "asset_count": 0}}}',
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {
                    "WIZ_TO_NOTION_SOURCE_DIR": str(source),
                    "NOTION_PARENT_PAGE_ID": "11111111222233334444555555555555",
                },
                clear=True,
            ), mock.patch("wiz_to_notion.cli._default_dotenv_path", return_value=self._missing_dotenv_path()):
                first_exit_code = main(["import", "--dry-run", "--state", str(state)], stdout=stdout, stderr=stderr)

            self.assertEqual(0, first_exit_code)
            fingerprint = json.loads(stdout.getvalue())["notes"][0]["fingerprint"]
            state.write_text(
                f'{{"version": 1, "folders": {{}}, "notes": {{"One.md": {{"page_id": "page-1", "fingerprint": "{fingerprint}", "renderer_version": 5, "asset_count": 0}}}}}}',
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {
                    "WIZ_TO_NOTION_SOURCE_DIR": str(source),
                    "NOTION_PARENT_PAGE_ID": "11111111222233334444555555555555",
                },
                clear=True,
            ), mock.patch("wiz_to_notion.cli._default_dotenv_path", return_value=self._missing_dotenv_path()):
                exit_code = main(["import", "--dry-run", "--state", str(state)], stdout=stdout, stderr=stderr)

        self.assertEqual(0, exit_code)
        summary = stderr.getvalue()
        self.assertIn("Dry run complete.", summary)
        self.assertIn("Would import: 0", summary)
        self.assertIn("Skipped unchanged: 1", summary)
        self.assertIn("Failed: 0", summary)

    def test_import_reports_notion_permission_errors_without_traceback(self) -> None:
        from wiz_to_notion.cli import main
        from wiz_to_notion.notion import NotionApiError

        class FailingClient:
            def __init__(self, *args, **kwargs) -> None:
                return None

            def create_page(self, **kwargs):
                raise NotionApiError(
                    404,
                    "object_not_found",
                    'Could not find page with ID: parent. Make sure the relevant pages and databases are shared with your integration "scenod-brain".',
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            (source / "AI").mkdir()
            (source / "AI" / "One.md").write_text("# One", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "WIZ_TO_NOTION_SOURCE_DIR": str(source),
                        "NOTION_PARENT_PAGE_ID": "11111111222233334444555555555555",
                        "NOTION_TOKEN": "secret",
                    },
                    clear=True,
                ),
                mock.patch("wiz_to_notion.cli._default_dotenv_path", return_value=self._missing_dotenv_path()),
                mock.patch("wiz_to_notion.cli.NotionClient", FailingClient),
            ):
                exit_code = main(["import", "--limit", "1", "--skip-assets"], stdout=stdout, stderr=stderr)

        self.assertEqual(1, exit_code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("Notion API error 404 object_not_found", stderr.getvalue())
        self.assertIn("Add connections", stderr.getvalue())
        self.assertIn("scenod-brain", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
