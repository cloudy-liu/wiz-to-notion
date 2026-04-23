from __future__ import annotations

import unittest


class MarkdownBlockConversionTests(unittest.TestCase):
    def test_converts_common_markdown_blocks(self) -> None:
        from wiz_to_notion.blocks import markdown_to_blocks

        blocks = markdown_to_blocks(
            "\n".join(
                [
                    "# Title",
                    "",
                    "[Docs](https://example.com) 最应该阅读",
                    "",
                    "## 反思",
                    "",
                    "> 2026/4/18",
                    "",
                    "1. 第一条",
                    "* 收获：小步迭代",
                    "",
                    "- [x] done",
                    "- [ ] todo",
                    "",
                    "```shell",
                    "echo hi",
                    "```",
                    "",
                    "---",
                ]
            )
        )

        self.assertEqual(
            [
                "heading_1",
                "paragraph",
                "heading_2",
                "quote",
                "numbered_list_item",
                "bulleted_list_item",
                "to_do",
                "to_do",
                "code",
                "divider",
            ],
            [block["type"] for block in blocks],
        )
        paragraph = blocks[1]["paragraph"]["rich_text"]
        self.assertEqual("Docs", paragraph[0]["text"]["content"])
        self.assertEqual({"url": "https://example.com"}, paragraph[0]["text"]["link"])
        self.assertEqual("shell", blocks[8]["code"]["language"])
        self.assertTrue(blocks[6]["to_do"]["checked"])
        self.assertFalse(blocks[7]["to_do"]["checked"])

    def test_splits_long_paragraphs_without_losing_text(self) -> None:
        from wiz_to_notion.blocks import markdown_to_blocks

        text = "x" * 4500
        blocks = markdown_to_blocks(text)

        self.assertEqual(["paragraph", "paragraph", "paragraph"], [block["type"] for block in blocks])
        joined = "".join(block["paragraph"]["rich_text"][0]["text"]["content"] for block in blocks)
        self.assertEqual(text, joined)

    def test_accepts_plain_text_code_fence_language(self) -> None:
        from wiz_to_notion.blocks import markdown_to_blocks

        blocks = markdown_to_blocks("```plain text\nhello\n```\n\n## Next")

        self.assertEqual(["code", "heading_2"], [block["type"] for block in blocks])
        self.assertEqual("plain text", blocks[0]["code"]["language"])
        self.assertEqual("hello", blocks[0]["code"]["rich_text"][0]["text"]["content"])

    def test_ignores_stray_empty_fence_before_heading(self) -> None:
        from wiz_to_notion.blocks import markdown_to_blocks

        blocks = markdown_to_blocks("使用 ASCII 风格：\n```\n\n#### 全局\n\n正文")

        self.assertEqual(["paragraph", "heading_3", "paragraph"], [block["type"] for block in blocks])
        heading_text = blocks[1]["heading_3"]["rich_text"][0]["text"]["content"]
        self.assertEqual("全局", heading_text)

    def test_converts_inline_bold_italic_and_code(self) -> None:
        from wiz_to_notion.blocks import rich_text_from_markdown

        rich = rich_text_from_markdown("1. **本质**：`headless` 和 *cli*")

        rendered = [(item["text"]["content"], item.get("annotations", {})) for item in rich]
        self.assertIn(("本质", {"bold": True}), rendered)
        self.assertIn(("headless", {"code": True}), rendered)
        self.assertIn(("cli", {"italic": True}), rendered)

    def test_converts_formatting_inside_links(self) -> None:
        from wiz_to_notion.blocks import rich_text_from_markdown

        rich = rich_text_from_markdown("[**Secret**](https://example.com)")

        self.assertEqual("Secret", rich[0]["text"]["content"])
        self.assertEqual({"url": "https://example.com"}, rich[0]["text"]["link"])
        self.assertEqual({"bold": True}, rich[0]["annotations"])

    def test_converts_links_inside_bold_text(self) -> None:
        from wiz_to_notion.blocks import rich_text_from_markdown

        rich = rich_text_from_markdown("**[Docs](https://example.com)/[API](https://api.example.com)**")

        self.assertEqual("Docs", rich[0]["text"]["content"])
        self.assertEqual({"url": "https://example.com"}, rich[0]["text"]["link"])
        self.assertEqual({"bold": True}, rich[0]["annotations"])
        self.assertEqual("/", rich[1]["text"]["content"])
        self.assertEqual({"bold": True}, rich[1]["annotations"])
        self.assertEqual("API", rich[2]["text"]["content"])
        self.assertEqual({"url": "https://api.example.com"}, rich[2]["text"]["link"])
        self.assertEqual({"bold": True}, rich[2]["annotations"])

    def test_converts_link_labels_with_nested_brackets(self) -> None:
        from wiz_to_notion.blocks import rich_text_from_markdown

        rich = rich_text_from_markdown("[Guide [LWN.net]](https://example.com)")

        self.assertEqual("Guide [LWN.net]", rich[0]["text"]["content"])
        self.assertEqual({"url": "https://example.com"}, rich[0]["text"]["link"])

    def test_converts_markdown_tables(self) -> None:
        from wiz_to_notion.blocks import markdown_to_blocks

        blocks = markdown_to_blocks("| Name | Link |\n| --- | --- |\n| Docs | [API](https://example.com) |")

        self.assertEqual(["table"], [block["type"] for block in blocks])
        table = blocks[0]["table"]
        self.assertEqual(2, table["table_width"])
        self.assertTrue(table["has_column_header"])
        self.assertFalse(table["has_row_header"])
        self.assertEqual("Name", table["children"][0]["table_row"]["cells"][0][0]["text"]["content"])
        link_cell = table["children"][1]["table_row"]["cells"][1][0]
        self.assertEqual("API", link_cell["text"]["content"])
        self.assertEqual({"url": "https://example.com"}, link_cell["text"]["link"])

    def test_converts_standalone_external_images(self) -> None:
        from wiz_to_notion.blocks import markdown_to_blocks

        blocks = markdown_to_blocks("![Diagram](https://example.com/diagram.png)")

        self.assertEqual(["image"], [block["type"] for block in blocks])
        self.assertEqual("external", blocks[0]["image"]["type"])
        self.assertEqual("https://example.com/diagram.png", blocks[0]["image"]["external"]["url"])
        self.assertEqual("Diagram", blocks[0]["image"]["caption"][0]["text"]["content"])


if __name__ == "__main__":
    unittest.main()
