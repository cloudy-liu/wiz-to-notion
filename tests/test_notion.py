from __future__ import annotations

import unittest
from pathlib import Path


class NotionClientHelpersTests(unittest.TestCase):
    def test_extract_page_id_accepts_notion_url_and_raw_id(self) -> None:
        from wiz_to_notion.notion import extract_page_id

        self.assertEqual(
            "3487344d-42e7-808a-9a14-c9787050ab7a",
            extract_page_id("https://www.notion.so/Android-3487344d42e7808a9a14c9787050ab7a"),
        )
        self.assertEqual(
            "3487344d-42e7-808a-9a14-c9787050ab7a",
            extract_page_id("3487344d42e7808a9a14c9787050ab7a"),
        )

    def test_create_page_payload_uses_markdown_without_children(self) -> None:
        from wiz_to_notion.notion import build_create_page_payload

        payload = build_create_page_payload(
            parent_page_id="parent-id",
            title="Roadmap",
            markdown="# Roadmap\n\nBody",
        )

        self.assertEqual({"type": "page_id", "page_id": "parent-id"}, payload["parent"])
        self.assertEqual("Roadmap", payload["properties"]["title"]["title"][0]["text"]["content"])
        self.assertEqual("# Roadmap\n\nBody", payload["markdown"])
        self.assertNotIn("children", payload)

    def test_asset_block_uses_file_upload_type_for_images_and_files(self) -> None:
        from wiz_to_notion.notion import build_file_block

        image_block = build_file_block("upload-1", Path("cover.png"), caption="cover.png")
        file_block = build_file_block("upload-2", Path("deck.xmind"), caption="deck.xmind")

        self.assertEqual("image", image_block["type"])
        self.assertEqual({"id": "upload-1"}, image_block["image"]["file_upload"])
        self.assertEqual("file", file_block["type"])
        self.assertEqual({"id": "upload-2"}, file_block["file"]["file_upload"])

    def test_asset_block_omits_caption_when_not_provided(self) -> None:
        from wiz_to_notion.notion import build_file_block

        image_block = build_file_block("upload-1", Path("cover.png"))

        self.assertEqual("image", image_block["type"])
        self.assertNotIn("caption", image_block["image"])


if __name__ == "__main__":
    unittest.main()
