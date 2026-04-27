from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class NotionClientHelpersTests(unittest.TestCase):
    def test_extract_page_id_accepts_notion_url_and_raw_id(self) -> None:
        from wiz_to_notion.notion import extract_page_id

        self.assertEqual(
            "11111111-2222-3333-4444-555555555555",
            extract_page_id("https://www.notion.so/workspace/Import-Target-11111111222233334444555555555555"),
        )
        self.assertEqual(
            "11111111-2222-3333-4444-555555555555",
            extract_page_id("11111111222233334444555555555555"),
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

    def test_update_page_payload_can_erase_content_and_update_title(self) -> None:
        from wiz_to_notion.notion import build_update_page_payload

        payload = build_update_page_payload(title="Roadmap", erase_content=True)

        self.assertEqual("Roadmap", payload["properties"]["title"]["title"][0]["text"]["content"])
        self.assertTrue(payload["erase_content"])

    def test_update_page_markdown_payload_replaces_page_content(self) -> None:
        from wiz_to_notion.notion import build_update_page_markdown_payload

        payload = build_update_page_markdown_payload(markdown="# Roadmap\n\nBody")

        self.assertEqual("replace_content", payload["type"])
        self.assertEqual("# Roadmap\n\nBody", payload["replace_content"]["new_str"])

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

    def test_upload_file_reuses_original_content_type_from_create_response(self) -> None:
        from wiz_to_notion.notion import NotionClient

        class FakeResponse:
            def __init__(self, status_code: int, payload: dict[str, object]) -> None:
                self.status_code = status_code
                self._payload = payload
                self.content = json.dumps(payload).encode("utf-8")
                self.text = self.content.decode("utf-8")
                self.headers: dict[str, str] = {}
                self.reason = "OK"

            def json(self) -> dict[str, object]:
                return self._payload

        class RecordingSession:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def request(self, method: str, url: str, *, headers: dict[str, str], json: dict[str, object] | None = None, files: object = None) -> FakeResponse:
                self.calls.append(
                    {
                        "method": method,
                        "url": url,
                        "headers": headers,
                        "json": json,
                        "files": files,
                    }
                )
                if url.endswith("/file_uploads"):
                    return FakeResponse(200, {"id": "upload-1", "content_type": "application/x-7z-compressed"})
                return FakeResponse(200, {"id": "upload-1"})

        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "archive.7z"
            archive.write_bytes(b"7z")
            session = RecordingSession()
            client = NotionClient(token="secret", notion_version="2022-06-28", session=session, request_delay=0.0)

            upload_id = client.upload_file(archive)

        self.assertEqual("upload-1", upload_id)
        self.assertEqual(2, len(session.calls))
        file_tuple = session.calls[1]["files"]["file"]
        self.assertEqual("application/x-7z-compressed", file_tuple[2])

    def test_request_retries_transient_cloudflare_403(self) -> None:
        from wiz_to_notion.notion import NotionClient

        class FakeResponse:
            def __init__(self, status_code: int, *, payload: dict[str, object] | None = None, text: str = "", headers: dict[str, str] | None = None, reason: str = "") -> None:
                self.status_code = status_code
                self._payload = payload
                self.text = text
                self.content = text.encode("utf-8") if text else (json.dumps(payload).encode("utf-8") if payload is not None else b"")
                self.headers = headers or {}
                self.reason = reason

            def json(self) -> dict[str, object]:
                if self._payload is None:
                    raise ValueError("not json")
                return self._payload

        class SequencedSession:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method: str, url: str, *, headers: dict[str, str], json: dict[str, object] | None = None, files: object = None) -> FakeResponse:
                self.calls += 1
                if self.calls == 1:
                    return FakeResponse(
                        403,
                        text="<html><title>Attention Required! | Cloudflare</title><body>Sorry, you have been blocked</body></html>",
                        reason="Forbidden",
                    )
                return FakeResponse(200, payload={"id": "page-1", "url": "https://notion.local/page-1"})

        sleeps: list[float] = []
        client = NotionClient(
            token="secret",
            notion_version="2022-06-28",
            session=SequencedSession(),
            request_delay=0.0,
            sleep_fn=sleeps.append,
        )

        page = client.create_page(parent_page_id="parent-id", title="Roadmap", markdown="# Roadmap")

        self.assertEqual("page-1", page["id"])
        self.assertEqual([1.0], sleeps)


if __name__ == "__main__":
    unittest.main()
