from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class FakeNotionClient:
    def __init__(self) -> None:
        self.created_pages: list[dict[str, object]] = []
        self.updated_pages: list[dict[str, object]] = []
        self.updated_markdown_pages: list[dict[str, object]] = []
        self.uploaded_files: list[Path] = []
        self.appended_blocks: list[tuple[str, list[dict[str, object]]]] = []

    def create_page(self, *, parent_page_id: str, title: str, markdown: str | None = None) -> dict[str, object]:
        page_id = f"page-{len(self.created_pages) + 1}"
        self.created_pages.append(
            {
                "id": page_id,
                "parent_page_id": parent_page_id,
                "title": title,
                "markdown": markdown,
            }
        )
        return {"id": page_id, "url": f"https://notion.local/{page_id}"}

    def upload_file(self, path: Path) -> str:
        self.uploaded_files.append(path)
        return f"upload-{len(self.uploaded_files)}"

    def append_blocks(self, block_id: str, children: list[dict[str, object]]) -> dict[str, object]:
        self.appended_blocks.append((block_id, children))
        return {"object": "list"}

    def update_page(
        self,
        *,
        page_id: str,
        title: str | None = None,
        erase_content: bool = False,
    ) -> dict[str, object]:
        self.updated_pages.append(
            {
                "page_id": page_id,
                "title": title,
                "erase_content": erase_content,
            }
        )
        return {"id": page_id, "url": f"https://notion.local/{page_id}"}

    def update_page_markdown(self, *, page_id: str, markdown: str) -> dict[str, object]:
        self.updated_markdown_pages.append(
            {
                "page_id": page_id,
                "markdown": markdown,
            }
        )
        return {"object": "page_markdown", "id": page_id, "markdown": markdown, "truncated": False, "unknown_block_ids": []}

    def list_block_children(self, block_id: str, *, start_cursor: str | None = None, page_size: int = 100) -> dict[str, object]:
        return {"results": [], "has_more": False, "next_cursor": None}

    def iter_block_children(self, block_id: str, *, page_size: int = 100):
        start_cursor: str | None = None
        while True:
            response = self.list_block_children(block_id, start_cursor=start_cursor, page_size=page_size)
            for item in response.get("results", []):
                yield item
            if not response.get("has_more"):
                return
            start_cursor = response.get("next_cursor")
            if not start_cursor:
                return


class ImporterTests(unittest.TestCase):
    ROOT_PAGE_ID = "3487344d42e7808a9a14c9787050ab7a"
    ROOT_PAGE_ID_DASHED = "3487344d-42e7-808a-9a14-c9787050ab7a"

    def test_import_creates_folder_pages_note_pages_assets_in_place_and_state(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "AI" / "Roadmap.md"
            image = source / "_wiz" / "resources" / "doc-1" / "cover.png"
            note.parent.mkdir(parents=True)
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            note.write_text(
                "---\ntitle: Roadmap\n---\n\n# Roadmap\n\nBefore\n\n![](../_wiz/resources/doc-1/cover.png)\n\nAfter",
                encoding="utf-8",
            )
            state_path = source / "_wiz" / "notion_import_state.json"
            report_path = source / "_wiz" / "notion_import_report.json"
            fake_client = FakeNotionClient()

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=state_path,
                report_path=report_path,
            )

            self.assertEqual(1, result.summary.imported_notes)
            self.assertEqual(1, result.summary.created_folders)
            self.assertEqual(1, result.summary.uploaded_assets)
            self.assertEqual(["AI", "Roadmap"], [call["title"] for call in fake_client.created_pages])
            self.assertEqual(self.ROOT_PAGE_ID_DASHED, fake_client.created_pages[0]["parent_page_id"])
            self.assertEqual("page-1", fake_client.created_pages[1]["parent_page_id"])
            self.assertEqual([image], fake_client.uploaded_files)
            self.assertEqual("page-2", fake_client.appended_blocks[0][0])
            appended_types = [block["type"] for block in fake_client.appended_blocks[0][1]]
            self.assertEqual(["heading_1", "paragraph", "image", "paragraph"], appended_types)
            self.assertEqual("upload-1", fake_client.appended_blocks[0][1][2]["image"]["file_upload"]["id"])
            self.assertNotIn("caption", fake_client.appended_blocks[0][1][2]["image"])
            appended_text = "".join(
                rich_text["text"]["content"]
                for block in fake_client.appended_blocks[0][1]
                for rich_text in block[block["type"]].get("rich_text", [])
            )
            self.assertNotIn("Image imported below", appended_text)
            self.assertTrue(state_path.exists())
            self.assertTrue(report_path.exists())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("AI/Roadmap.md", state["notes"])

    def test_resume_skips_already_imported_matching_note(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "AI" / "Roadmap.md"
            note.parent.mkdir(parents=True)
            note.write_text("---\ntitle: Roadmap\n---\n\n# Roadmap", encoding="utf-8")
            state_path = source / "_wiz" / "notion_import_state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "folders": {"AI": {"page_id": "page-folder", "title": "AI"}},
                        "notes": {
                            "AI/Roadmap.md": {
                                "page_id": "page-note",
                                "title": "Roadmap",
                                "fingerprint": "placeholder",
                                "renderer_version": 5,
                                "asset_count": 0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            fake_client = FakeNotionClient()

            first = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=state_path,
                report_path=source / "_wiz" / "report.json",
                dry_run=True,
            )
            fingerprint = first.notes[0].fingerprint
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["notes"]["AI/Roadmap.md"]["fingerprint"] = fingerprint
            state_path.write_text(json.dumps(state), encoding="utf-8")

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=state_path,
                report_path=source / "_wiz" / "report.json",
            )

            self.assertEqual(0, result.summary.imported_notes)
            self.assertEqual(1, result.summary.skipped_notes)
            self.assertEqual([], fake_client.created_pages)

    def test_import_falls_back_to_plain_blocks_when_notion_rejects_markdown(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree
        from wiz_to_notion.notion import NotionApiError

        class MarkdownRejectingClient(FakeNotionClient):
            def create_page(self, *, parent_page_id: str, title: str, markdown: str | None = None) -> dict[str, object]:
                if markdown:
                    raise NotionApiError(400, "validation_error", "Failed to parse markdown content: Failed to create block")
                return super().create_page(parent_page_id=parent_page_id, title=title, markdown=markdown)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            note.write_text("# Roadmap\n\n" + ("Long markdown body " * 200), encoding="utf-8")
            fake_client = MarkdownRejectingClient()

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=source / "_wiz" / "state.json",
                report_path=source / "_wiz" / "report.json",
            )

        self.assertEqual(1, result.summary.imported_notes)
        self.assertEqual("imported_with_blocks", result.notes[0].status)
        self.assertEqual("Roadmap", fake_client.created_pages[0]["title"])
        self.assertIsNone(fake_client.created_pages[0]["markdown"])
        self.assertEqual("page-1", fake_client.appended_blocks[0][0])
        self.assertEqual("heading_1", fake_client.appended_blocks[0][1][0]["type"])

    def test_old_renderer_state_does_not_skip_note(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree
        from wiz_to_notion.markdown import prepare_markdown_note

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            note.write_text("# Roadmap", encoding="utf-8")
            prepared = prepare_markdown_note(note, source)
            state_path = source / "_wiz" / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "folders": {},
                        "notes": {
                            "Roadmap.md": {
                                "page_id": "old-page",
                                "title": "Roadmap",
                                "fingerprint": prepared.fingerprint,
                                "asset_count": 0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            fake_client = FakeNotionClient()

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=state_path,
                report_path=source / "_wiz" / "report.json",
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(1, result.summary.imported_notes)
        self.assertEqual("updated", result.notes[0].status)
        self.assertEqual([], fake_client.created_pages)
        self.assertEqual([{"page_id": "old-page", "markdown": "# Roadmap"}], fake_client.updated_markdown_pages)
        self.assertEqual(5, state["notes"]["Roadmap.md"]["renderer_version"])

    def test_resume_updates_existing_note_imported_without_assets_to_place_assets(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree
        from wiz_to_notion.markdown import prepare_markdown_note

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            image = source / "_wiz" / "resources" / "doc-1" / "cover.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            note.write_text("# Roadmap\n\n![](_wiz/resources/doc-1/cover.png)", encoding="utf-8")
            prepared = prepare_markdown_note(note, source)
            state_path = source / "_wiz" / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "folders": {},
                        "notes": {
                            "Roadmap.md": {
                                "page_id": "page-note",
                                "title": "Roadmap",
                                "fingerprint": prepared.fingerprint,
                                "renderer_version": 5,
                                "asset_count": 1,
                                "uploaded_assets": 0,
                                "skipped_assets": 1,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            fake_client = FakeNotionClient()

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=state_path,
                report_path=source / "_wiz" / "report.json",
                upload_assets=True,
            )

        self.assertEqual(1, result.summary.imported_notes)
        self.assertEqual("updated_with_blocks", result.notes[0].status)
        self.assertEqual([], fake_client.created_pages)
        self.assertEqual(
            [{"page_id": "page-note", "title": "Roadmap", "erase_content": True}],
            fake_client.updated_pages,
        )
        self.assertEqual([image], fake_client.uploaded_files)
        self.assertEqual("page-note", fake_client.appended_blocks[0][0])
        self.assertEqual(["heading_1", "image"], [block["type"] for block in fake_client.appended_blocks[0][1]])
        self.assertNotIn("caption", fake_client.appended_blocks[0][1][1]["image"])

    def test_resume_updates_changed_note_in_place_instead_of_creating_duplicate_page(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            note.write_text("# Roadmap\n\nUpdated body", encoding="utf-8")
            state_path = source / "_wiz" / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "folders": {},
                        "notes": {
                            "Roadmap.md": {
                                "page_id": "existing-page",
                                "title": "Roadmap",
                                "fingerprint": "stale-fingerprint",
                                "renderer_version": 5,
                                "asset_count": 0,
                                "uploaded_assets": 0,
                                "skipped_assets": 0,
                                "asset_upload_attempted": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            fake_client = FakeNotionClient()

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=state_path,
                report_path=source / "_wiz" / "report.json",
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(1, result.summary.imported_notes)
        self.assertEqual([], fake_client.created_pages)
        self.assertEqual(
            [{"page_id": "existing-page", "markdown": "# Roadmap\n\nUpdated body"}],
            fake_client.updated_markdown_pages,
        )
        self.assertEqual([], fake_client.appended_blocks)
        self.assertEqual("existing-page", state["notes"]["Roadmap.md"]["page_id"])

    def test_resume_updates_changed_asset_note_in_place_without_creating_duplicate_page(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            image = source / "_wiz" / "resources" / "doc-1" / "cover.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            note.write_text("# Roadmap\n\n![](_wiz/resources/doc-1/cover.png)", encoding="utf-8")
            state_path = source / "_wiz" / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "folders": {},
                        "notes": {
                            "Roadmap.md": {
                                "page_id": "existing-page",
                                "title": "Roadmap",
                                "fingerprint": "stale-fingerprint",
                                "renderer_version": 5,
                                "asset_count": 1,
                                "uploaded_assets": 1,
                                "skipped_assets": 0,
                                "asset_upload_attempted": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            fake_client = FakeNotionClient()

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=state_path,
                report_path=source / "_wiz" / "report.json",
                upload_assets=True,
            )

        self.assertEqual(1, result.summary.imported_notes)
        self.assertEqual([], fake_client.created_pages)
        self.assertEqual(
            [{"page_id": "existing-page", "title": "Roadmap", "erase_content": True}],
            fake_client.updated_pages,
        )
        self.assertEqual([image], fake_client.uploaded_files)
        self.assertEqual("existing-page", fake_client.appended_blocks[0][0])
        self.assertEqual(["heading_1", "image"], [block["type"] for block in fake_client.appended_blocks[0][1]])

    def test_recreates_state_folder_when_existing_folder_is_archived(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree
        from wiz_to_notion.notion import NotionApiError

        class ArchivedFolderClient(FakeNotionClient):
            def create_page(self, *, parent_page_id: str, title: str, markdown: str | None = None) -> dict[str, object]:
                if parent_page_id == "archived-folder":
                    raise NotionApiError(
                        400,
                        "validation_error",
                        "Can't edit block that is archived. You must unarchive the block before editing.",
                    )
                return super().create_page(parent_page_id=parent_page_id, title=title, markdown=markdown)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "AI" / "Roadmap.md"
            note.parent.mkdir(parents=True)
            note.write_text("# Roadmap", encoding="utf-8")
            state_path = source / "_wiz" / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "folders": {
                            "AI": {
                                "page_id": "archived-folder",
                                "title": "AI",
                            }
                        },
                        "notes": {},
                    }
                ),
                encoding="utf-8",
            )
            fake_client = ArchivedFolderClient()

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=state_path,
                report_path=source / "_wiz" / "report.json",
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(1, result.summary.imported_notes)
        self.assertEqual(1, result.summary.created_folders)
        self.assertEqual(["AI", "Roadmap"], [page["title"] for page in fake_client.created_pages])
        self.assertEqual("page-1", fake_client.created_pages[1]["parent_page_id"])
        self.assertEqual("page-1", state["folders"]["AI"]["page_id"])

    def test_import_reports_stage_progress_during_asset_upload(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            image = source / "_wiz" / "resources" / "doc-1" / "cover.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            note.write_text("# Roadmap\n\n![](_wiz/resources/doc-1/cover.png)", encoding="utf-8")
            messages: list[str] = []
            fake_client = FakeNotionClient()

            import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=source / "_wiz" / "state.json",
                report_path=source / "_wiz" / "report.json",
                progress=messages.append,
            )

        self.assertTrue(messages)
        self.assertEqual("1/1 Roadmap.md", messages[0])
        self.assertTrue(any("creating page" in message for message in messages))
        self.assertTrue(any("uploading asset 1/1" in message for message in messages))
        self.assertTrue(any("appending" in message for message in messages))

    def test_import_throttles_asset_progress_messages(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            lines = ["# Roadmap", ""]
            for index in range(1, 12):
                image = source / "_wiz" / "resources" / "doc-1" / f"cover-{index}.png"
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b"png")
                lines.append(f"![](_wiz/resources/doc-1/cover-{index}.png)")
                lines.append("")

            note.write_text("\n".join(lines), encoding="utf-8")
            messages: list[str] = []
            fake_client = FakeNotionClient()

            import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=source / "_wiz" / "state.json",
                report_path=source / "_wiz" / "report.json",
                progress=messages.append,
            )

        asset_messages = [message for message in messages if "uploading asset" in message]
        self.assertEqual(
            [
                "1/1 Roadmap.md - uploading asset 1/11: cover-1.png",
                "1/1 Roadmap.md - uploading asset 5/11: cover-5.png",
                "1/1 Roadmap.md - uploading asset 10/11: cover-10.png",
                "1/1 Roadmap.md - uploading asset 11/11: cover-11.png",
            ],
            asset_messages,
        )

    def test_import_skips_asset_when_notion_rejects_unsupported_extension(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree
        from wiz_to_notion.notion import NotionApiError

        class UnsupportedExtensionClient(FakeNotionClient):
            def upload_file(self, path: Path) -> str:
                if path.suffix.lower() == ".xmind":
                    raise NotionApiError(
                        400,
                        "validation_error",
                        "Provided `filename` has an extension that is not supported for the File Upload API.",
                    )
                return super().upload_file(path)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            image = source / "_wiz" / "resources" / "doc-1" / "cover.png"
            mindmap = source / "_wiz" / "resources" / "doc-1" / "plan.xmind"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            mindmap.write_bytes(b"xmind")
            note.write_text(
                "# Roadmap\n\n![](_wiz/resources/doc-1/cover.png)\n\n[plan](_wiz/resources/doc-1/plan.xmind)",
                encoding="utf-8",
            )
            fake_client = UnsupportedExtensionClient()

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=source / "_wiz" / "state.json",
                report_path=source / "_wiz" / "report.json",
            )

        self.assertEqual(1, result.summary.imported_notes)
        self.assertEqual(0, result.summary.failed_notes)
        self.assertEqual(1, result.summary.uploaded_assets)
        self.assertEqual(1, result.summary.skipped_assets)
        self.assertEqual(["heading_1", "image", "paragraph"], [block["type"] for block in fake_client.appended_blocks[0][1]])
        self.assertIn("Attachment imported below: plan.xmind", fake_client.appended_blocks[0][1][2]["paragraph"]["rich_text"][0]["text"]["content"])

    def test_import_skips_asset_when_notion_rejects_workspace_size_limit(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree
        from wiz_to_notion.notion import NotionApiError

        class SizeLimitClient(FakeNotionClient):
            def upload_file(self, path: Path) -> str:
                raise NotionApiError(
                    400,
                    "validation_error",
                    "File size of 6.756 MiB exceeds the limit of 5 MiB.",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            image = source / "_wiz" / "resources" / "doc-1" / "cover.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            note.write_text("# Roadmap\n\n![](_wiz/resources/doc-1/cover.png)", encoding="utf-8")
            fake_client = SizeLimitClient()

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=source / "_wiz" / "state.json",
                report_path=source / "_wiz" / "report.json",
            )

        self.assertEqual(1, result.summary.imported_notes)
        self.assertEqual(0, result.summary.failed_notes)
        self.assertEqual(0, result.summary.uploaded_assets)
        self.assertEqual(1, result.summary.skipped_assets)
        self.assertEqual(["heading_1", "paragraph"], [block["type"] for block in fake_client.appended_blocks[0][1]])
        self.assertIn("Image imported below: cover.png", fake_client.appended_blocks[0][1][1]["paragraph"]["rich_text"][0]["text"]["content"])

    def test_failed_initial_asset_import_records_page_id_and_retry_updates_same_page(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree

        class FailingAppendClient(FakeNotionClient):
            def __init__(self) -> None:
                super().__init__()
                self.fail_append_once = True

            def append_blocks(self, block_id: str, children: list[dict[str, object]]) -> dict[str, object]:
                if self.fail_append_once:
                    self.fail_append_once = False
                    raise RuntimeError("simulated append failure")
                return super().append_blocks(block_id, children)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            image = source / "_wiz" / "resources" / "doc-1" / "cover.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            note.write_text("# Roadmap\n\n![](_wiz/resources/doc-1/cover.png)", encoding="utf-8")
            state_path = source / "_wiz" / "state.json"
            report_path = source / "_wiz" / "report.json"
            fake_client = FailingAppendClient()

            first = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=state_path,
                report_path=report_path,
            )
            state_after_first = json.loads(state_path.read_text(encoding="utf-8"))

            second = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=state_path,
                report_path=report_path,
            )

        self.assertEqual(1, first.summary.failed_notes)
        self.assertEqual("page-1", state_after_first["notes"]["Roadmap.md"]["page_id"])
        self.assertEqual(1, len(fake_client.created_pages))
        self.assertEqual(1, second.summary.imported_notes)
        self.assertEqual([], fake_client.created_pages[1:])
        self.assertEqual(
            [{"page_id": "page-1", "title": "Roadmap", "erase_content": True}],
            fake_client.updated_pages,
        )
        self.assertEqual("page-1", fake_client.appended_blocks[0][0])

    def test_reuses_existing_unique_live_page_when_state_entry_is_missing(self) -> None:
        from wiz_to_notion.importer import import_markdown_tree

        class DiscoveringClient(FakeNotionClient):
            ROOT_ID = ImporterTests.ROOT_PAGE_ID_DASHED

            def list_block_children(self, block_id: str, *, start_cursor: str | None = None, page_size: int = 100) -> dict[str, object]:
                if block_id == self.ROOT_ID:
                    return {
                        "results": [
                            {
                                "id": "existing-page",
                                "type": "child_page",
                                "child_page": {"title": "Roadmap"},
                                "in_trash": False,
                                "created_time": "2026-04-26T00:00:00.000Z",
                                "last_edited_time": "2026-04-26T00:00:00.000Z",
                            }
                        ],
                        "has_more": False,
                        "next_cursor": None,
                    }
                return {"results": [], "has_more": False, "next_cursor": None}

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            note = source / "Roadmap.md"
            note.write_text("# Roadmap\n\nUpdated body", encoding="utf-8")
            fake_client = DiscoveringClient()

            result = import_markdown_tree(
                source_dir=source,
                parent_page_id=self.ROOT_PAGE_ID,
                notion_client=fake_client,
                state_path=source / "_wiz" / "state.json",
                report_path=source / "_wiz" / "report.json",
            )

        self.assertEqual(1, result.summary.imported_notes)
        self.assertEqual([], fake_client.created_pages)
        self.assertEqual(
            [{"page_id": "existing-page", "markdown": "# Roadmap\n\nUpdated body"}],
            fake_client.updated_markdown_pages,
        )


if __name__ == "__main__":
    unittest.main()
