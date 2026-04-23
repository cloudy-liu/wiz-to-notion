from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class FakeNotionClient:
    def __init__(self) -> None:
        self.created_pages: list[dict[str, object]] = []
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
        self.assertEqual("imported", result.notes[0].status)
        self.assertEqual(["Roadmap"], [page["title"] for page in fake_client.created_pages])
        self.assertEqual(5, state["notes"]["Roadmap.md"]["renderer_version"])

    def test_resume_reimports_note_imported_without_assets_to_place_assets(self) -> None:
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
        self.assertEqual("imported_with_blocks", result.notes[0].status)
        self.assertEqual(["Roadmap"], [page["title"] for page in fake_client.created_pages])
        self.assertEqual([image], fake_client.uploaded_files)
        self.assertEqual("page-1", fake_client.appended_blocks[0][0])
        self.assertEqual(["heading_1", "image"], [block["type"] for block in fake_client.appended_blocks[0][1]])
        self.assertNotIn("caption", fake_client.appended_blocks[0][1][1]["image"])

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


if __name__ == "__main__":
    unittest.main()
