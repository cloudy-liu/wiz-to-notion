from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_environ = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_environ)

    def test_load_dotenv_sets_missing_values(self) -> None:
        from wiz_to_notion.cli import load_dotenv

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("NOTION_TOKEN=secret\nWIZ_TO_NOTION_SOURCE_DIR=D:\\notes\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                load_dotenv(env_path)
                self.assertEqual("secret", os.environ["NOTION_TOKEN"])
                self.assertEqual("D:\\notes", os.environ["WIZ_TO_NOTION_SOURCE_DIR"])

    def test_scan_command_reports_discovered_markdown_without_token(self) -> None:
        from wiz_to_notion.cli import main

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            (source / "A").mkdir()
            (source / "A" / "One.md").write_text("# One", encoding="utf-8")
            stdout = io.StringIO()

            exit_code = main(["scan", "--source", str(source)], stdout=stdout)

        self.assertEqual(0, exit_code)
        self.assertIn('"total_notes": 1', stdout.getvalue())

    def test_import_dry_run_uses_default_parent_url_and_source_env(self) -> None:
        from wiz_to_notion.cli import main

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            (source / "One.md").write_text("# One", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"WIZ_TO_NOTION_SOURCE_DIR": str(source)}, clear=True):
                exit_code = main(["import", "--dry-run"], stdout=stdout)

        self.assertEqual(0, exit_code)
        output = stdout.getvalue()
        self.assertIn('"dry_run": true', output)
        self.assertIn('"total_notes": 1', output)
        self.assertIn("3487344d-42e7-808a-9a14-c9787050ab7a", output)

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
                mock.patch.dict(os.environ, {"WIZ_TO_NOTION_SOURCE_DIR": str(source), "NOTION_TOKEN": "secret"}, clear=True),
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
