from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class MarkdownPreparationTests(unittest.TestCase):
    def test_strips_frontmatter_extracts_title_and_rewrites_local_assets(self) -> None:
        from wiz_to_notion.markdown import prepare_markdown_note

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note_path = root / "AI" / "Roadmap.md"
            image_path = root / "_wiz" / "resources" / "doc-1" / "cover.png"
            file_path = root / "_wiz" / "attachments" / "doc-1" / "deck.pdf"
            note_path.parent.mkdir(parents=True)
            image_path.parent.mkdir(parents=True)
            file_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"png")
            file_path.write_bytes(b"pdf")
            note_path.write_text(
                "\n".join(
                    [
                        "---",
                        "title: Product Roadmap",
                        "wiz_doc_guid: doc-1",
                        "---",
                        "",
                        "# Product Roadmap",
                        "",
                        "![](../_wiz/resources/doc-1/cover.png)",
                        "",
                        "[Deck](../_wiz/attachments/doc-1/deck.pdf)",
                        "",
                        "[Docs](https://example.com/docs)",
                    ]
                ),
                encoding="utf-8",
            )

            prepared = prepare_markdown_note(note_path, root)

        self.assertEqual("Product Roadmap", prepared.title)
        self.assertEqual("AI/Roadmap.md", prepared.relative_path.as_posix())
        self.assertNotIn("wiz_doc_guid", prepared.markdown)
        self.assertIn("# Product Roadmap", prepared.markdown)
        self.assertIn("[Image imported below: cover.png]", prepared.markdown)
        self.assertIn("[Attachment imported below: deck.pdf]", prepared.markdown)
        self.assertIn("[Docs](https://example.com/docs)", prepared.markdown)
        self.assertEqual(2, len(prepared.assets))
        self.assertEqual({"cover.png", "deck.pdf"}, {asset.path.name for asset in prepared.assets})

    def test_uses_filename_title_when_frontmatter_title_is_missing(self) -> None:
        from wiz_to_notion.markdown import prepare_markdown_note

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note_path = root / "Inbox" / "Untitled.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text("Body only", encoding="utf-8")

            prepared = prepare_markdown_note(note_path, root)

        self.assertEqual("Untitled", prepared.title)
        self.assertEqual("Body only", prepared.markdown)

    def test_strips_markdown_suffix_from_frontmatter_title(self) -> None:
        from wiz_to_notion.markdown import prepare_markdown_note

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note_path = root / "AI" / "Google ML.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text("---\ntitle: Google ML.md\n---\n\n# Google ML", encoding="utf-8")

            prepared = prepare_markdown_note(note_path, root)

        self.assertEqual("Google ML", prepared.title)

    def test_leaves_unresolved_relative_links_and_reports_them(self) -> None:
        from wiz_to_notion.markdown import prepare_markdown_note

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note_path = root / "Inbox" / "Links.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text("[Missing](missing.png)\n\n[Sibling](other.md)", encoding="utf-8")

            prepared = prepare_markdown_note(note_path, root)

        self.assertIn("[Missing](missing.png)", prepared.markdown)
        self.assertIn("[Sibling](other.md)", prepared.markdown)
        self.assertEqual(("missing.png", "other.md"), tuple(item.target for item in prepared.unresolved_links))

    def test_rewrites_linked_local_images_without_dangling_markdown(self) -> None:
        from wiz_to_notion.markdown import prepare_markdown_note

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note_path = root / "Article.md"
            image_path = root / "_wiz" / "resources" / "doc-1" / "cover.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"png")
            note_path.write_text(
                "[![](_wiz/resources/doc-1/cover.png)](https://example.com/article)",
                encoding="utf-8",
            )

            prepared = prepare_markdown_note(note_path, root)

        self.assertIn("[Image imported below: cover.png](https://example.com/article)", prepared.markdown)
        self.assertNotIn("]](", prepared.markdown)
        self.assertEqual(1, len(prepared.assets))

    def test_rewrites_oversized_external_links_and_wraps_long_lines(self) -> None:
        from wiz_to_notion.markdown import prepare_markdown_note

        long_url = "https://example.com/foo(bar)/" + ("a" * 2400)
        long_line = "x" * 2300
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            note_path = root / "Long.md"
            note_path.write_text(f"[Open]({long_url})\n\n{long_line}", encoding="utf-8")

            prepared = prepare_markdown_note(note_path, root)

        self.assertNotIn(f"[Open]({long_url})", prepared.markdown)
        self.assertNotIn("](", prepared.markdown)
        self.assertIn("Open", prepared.markdown)
        self.assertIn("Long link target:", prepared.markdown)
        self.assertIn("https://example.com/", prepared.markdown)
        self.assertLessEqual(max(len(line) for line in prepared.markdown.splitlines()), 1800)


if __name__ == "__main__":
    unittest.main()
