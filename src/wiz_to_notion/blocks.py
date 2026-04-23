from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


MAX_RICH_TEXT_CHARS = 1900
HEADING = re.compile(r"^(#{1,6})\s+(?P<text>.+?)\s*$")
ORDERED_LIST = re.compile(r"^\s*\d+[.)]\s+(?P<text>.+?)\s*$")
BULLETED_LIST = re.compile(r"^\s*[-*+]\s+(?P<text>.+?)\s*$")
TODO = re.compile(r"^\s*[-*+]\s+\[(?P<checked>[ xX])\]\s+(?P<text>.+?)\s*$")
FENCE = re.compile(r"^\s*```(?P<language>[^`]*)\s*$")
INLINE_TOKEN = re.compile(
    r"(?P<link>\[(?P<link_label>(?:[^\[\]\r\n]|\[[^\]\r\n]*\])*)\]\((?P<link_url>[^)\r\n]*)\))"
    r"|(?P<bold>\*\*(?P<bold_text>.+?)\*\*)"
    r"|(?P<code>`(?P<code_text>[^`]+?)`)"
    r"|(?P<italic>\*(?P<italic_text>[^*\n]+?)\*)"
)
IMAGE_LINE = re.compile(r"^\s*!\[(?P<caption>[^\]]*)\]\((?P<target>[^)\r\n]+)\)\s*$")
DIVIDER = re.compile(r"^\s*(?:---|\*\*\*|___)\s*$")
TABLE_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
IMAGE_EXTENSIONS = {
    ".apng",
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}

LANGUAGE_ALIASES = {
    "ps1": "powershell",
    "powershell": "powershell",
    "shell": "shell",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "cmd": "plain text",
    "bat": "plain text",
    "text": "plain text",
    "plaintext": "plain text",
    "plain text": "plain text",
    "": "plain text",
}


def _text_object(
    content: str,
    *,
    url: str | None = None,
    annotations: dict[str, bool] | None = None,
) -> dict[str, Any]:
    text: dict[str, Any] = {"content": content}
    if url:
        text["link"] = {"url": url}
    rich_text = {"type": "text", "text": text}
    if annotations:
        rich_text["annotations"] = annotations
    return rich_text


def _split_text(content: str, *, max_chars: int = MAX_RICH_TEXT_CHARS) -> list[str]:
    if not content:
        return []
    chunks: list[str] = []
    cursor = 0
    while cursor < len(content):
        end = min(len(content), cursor + max_chars)
        if end < len(content):
            split_at = max(content.rfind(" ", cursor, end), content.rfind("\n", cursor, end))
            if split_at > cursor + max_chars // 2:
                end = split_at
        chunk = content[cursor:end]
        if chunk:
            chunks.append(chunk)
        cursor = end
        if cursor < len(content) and content[cursor] == " ":
            cursor += 1
    return chunks


def _merge_annotations(
    annotations: dict[str, bool] | None,
    extra: dict[str, bool],
) -> dict[str, bool]:
    merged = dict(annotations or {})
    merged.update(extra)
    return merged


def _is_supported_link_target(target: str) -> bool:
    parsed = urlparse(target.strip())
    return parsed.scheme.lower() in {"http", "https", "mailto"}


def _clean_markdown_target(target: str) -> str:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if " " in target:
        target = target.split(None, 1)[0].strip()
    return target


def _is_external_image_target(target: str) -> bool:
    parsed = urlparse(target)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    return any(parsed.path.lower().endswith(extension) for extension in IMAGE_EXTENSIONS)


def _parse_inline_markdown(
    text: str,
    *,
    annotations: dict[str, bool] | None = None,
    url: str | None = None,
    depth: int = 0,
) -> list[dict[str, Any]]:
    rich_text: list[dict[str, Any]] = []

    def append_text(
        content: str,
    ) -> None:
        for chunk in _split_text(content):
            rich_text.append(_text_object(chunk, url=url, annotations=annotations))

    if depth > 8:
        append_text(text)
        return rich_text

    cursor = 0
    for match in INLINE_TOKEN.finditer(text):
        if match.start() > cursor:
            append_text(text[cursor : match.start()])
        if match.group("link"):
            label = match.group("link_label").strip() or match.group("link_url")
            target = _clean_markdown_target(match.group("link_url") or "")
            if _is_supported_link_target(target):
                rich_text.extend(
                    _parse_inline_markdown(
                        label,
                        annotations=annotations,
                        url=target,
                        depth=depth + 1,
                    )
                )
            elif target:
                rich_text.extend(
                    _parse_inline_markdown(
                        f"{label} ({target})",
                        annotations=annotations,
                        url=url,
                        depth=depth + 1,
                    )
                )
            else:
                rich_text.extend(
                    _parse_inline_markdown(
                        label,
                        annotations=annotations,
                        url=url,
                        depth=depth + 1,
                    )
                )
        elif match.group("bold"):
            rich_text.extend(
                _parse_inline_markdown(
                    match.group("bold_text"),
                    annotations=_merge_annotations(annotations, {"bold": True}),
                    url=url,
                    depth=depth + 1,
                )
            )
        elif match.group("code"):
            for chunk in _split_text(match.group("code_text")):
                rich_text.append(
                    _text_object(
                        chunk,
                        url=url,
                        annotations=_merge_annotations(annotations, {"code": True}),
                    )
                )
        elif match.group("italic"):
            rich_text.extend(
                _parse_inline_markdown(
                    match.group("italic_text"),
                    annotations=_merge_annotations(annotations, {"italic": True}),
                    url=url,
                    depth=depth + 1,
                )
            )
        cursor = match.end()
    if cursor < len(text):
        append_text(text[cursor:])
    return rich_text


def rich_text_from_markdown(text: str) -> list[dict[str, Any]]:
    return _parse_inline_markdown(text) or [_text_object("")]


def _block(block_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"object": "block", "type": block_type, block_type: payload or {}}


def _rich_text_block(block_type: str, text: str) -> dict[str, Any]:
    return _block(block_type, {"rich_text": rich_text_from_markdown(text)})


def _paragraph(text: str) -> dict[str, Any]:
    return _rich_text_block("paragraph", text)


def _paragraph_blocks(text: str) -> list[dict[str, Any]]:
    chunks = _split_text(text)
    return [_paragraph(chunk) for chunk in chunks] if chunks else []


def _heading(level: int, text: str) -> dict[str, Any]:
    block_type = f"heading_{max(1, min(level, 3))}"
    return _rich_text_block(block_type, text)


def _code_blocks(code: str, language: str) -> list[dict[str, Any]]:
    language_key = " ".join(language.strip().lower().split())
    normalized_language = LANGUAGE_ALIASES.get(language_key, language_key or "plain text")
    chunks = _split_text(code, max_chars=1900)
    return [
        _block(
            "code",
            {
                "rich_text": [_text_object(chunk)],
                "language": normalized_language,
            },
        )
        for chunk in chunks
    ]


def _image_block(target: str, caption: str) -> dict[str, Any]:
    return _block(
        "image",
        {
            "type": "external",
            "external": {"url": target},
            "caption": rich_text_from_markdown(caption) if caption.strip() else [],
        },
    )


def _split_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if "|" not in stripped:
        return None
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells = [cell.strip() for cell in stripped.split("|")]
    if len(cells) < 2:
        return None
    return cells


def _is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(TABLE_SEPARATOR_CELL.match(cell.replace(" ", "")) for cell in cells)


def _collect_table(lines: list[str], index: int) -> tuple[list[list[str]], int] | None:
    if index + 1 >= len(lines):
        return None
    header = _split_table_row(lines[index])
    separator = _split_table_row(lines[index + 1])
    if header is None or separator is None or not _is_table_separator(separator):
        return None

    rows = [header]
    next_index = index + 2
    while next_index < len(lines):
        if not lines[next_index].strip():
            break
        row = _split_table_row(lines[next_index])
        if row is None:
            break
        rows.append(row)
        next_index += 1
    return rows, next_index


def _table_block(rows: list[list[str]]) -> dict[str, Any]:
    table_width = max(len(row) for row in rows)
    children = []
    for row in rows:
        normalized_row = row + [""] * (table_width - len(row))
        children.append(
            _block(
                "table_row",
                {"cells": [rich_text_from_markdown(cell) for cell in normalized_row]},
            )
        )
    return _block(
        "table",
        {
            "table_width": table_width,
            "has_column_header": True,
            "has_row_header": False,
            "children": children,
        },
    )


def _looks_like_stray_empty_fence(lines: list[str], index: int, fence_match: re.Match[str]) -> bool:
    if fence_match.group("language").strip():
        return False

    next_index = index + 1
    saw_blank = False
    while next_index < len(lines) and not lines[next_index].strip():
        saw_blank = True
        next_index += 1
    if not saw_blank or next_index >= len(lines):
        return False

    next_line = lines[next_index]
    return bool(
        HEADING.match(next_line)
        or DIVIDER.match(next_line)
        or next_line.lstrip().startswith(">")
        or TODO.match(next_line)
        or ORDERED_LIST.match(next_line)
        or BULLETED_LIST.match(next_line)
    )


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    blocks: list[dict[str, Any]] = []
    paragraph_lines: list[str] = []
    in_code = False
    code_language = ""
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        paragraph = "\n".join(line.rstrip() for line in paragraph_lines).strip()
        paragraph_lines.clear()
        blocks.extend(_paragraph_blocks(paragraph))

    index = 0
    while index < len(lines):
        line = lines[index]
        fence_match = FENCE.match(line)
        if fence_match:
            if in_code:
                blocks.extend(_code_blocks("\n".join(code_lines), code_language))
                code_lines.clear()
                code_language = ""
                in_code = False
            elif _looks_like_stray_empty_fence(lines, index, fence_match):
                index += 1
                continue
            else:
                flush_paragraph()
                code_language = (fence_match.group("language") or "").strip()
                in_code = True
            index += 1
            continue

        if in_code:
            code_lines.append(line)
            index += 1
            continue

        if not line.strip():
            flush_paragraph()
            index += 1
            continue

        table = _collect_table(lines, index)
        if table is not None:
            rows, next_index = table
            flush_paragraph()
            blocks.append(_table_block(rows))
            index = next_index
            continue

        image_match = IMAGE_LINE.match(line)
        if image_match:
            image_target = _clean_markdown_target(image_match.group("target") or "")
            if _is_external_image_target(image_target):
                flush_paragraph()
                blocks.append(_image_block(image_target, image_match.group("caption") or ""))
                index += 1
                continue

        heading_match = HEADING.match(line)
        if heading_match:
            flush_paragraph()
            blocks.append(_heading(len(heading_match.group(1)), heading_match.group("text")))
            index += 1
            continue

        if DIVIDER.match(line):
            flush_paragraph()
            blocks.append(_block("divider"))
            index += 1
            continue

        if line.lstrip().startswith(">"):
            flush_paragraph()
            blocks.append(_rich_text_block("quote", line.lstrip()[1:].strip()))
            index += 1
            continue

        todo_match = TODO.match(line)
        if todo_match:
            flush_paragraph()
            blocks.append(
                _block(
                    "to_do",
                    {
                        "rich_text": rich_text_from_markdown(todo_match.group("text")),
                        "checked": todo_match.group("checked").lower() == "x",
                    },
                )
            )
            index += 1
            continue

        ordered_match = ORDERED_LIST.match(line)
        if ordered_match:
            flush_paragraph()
            blocks.append(_rich_text_block("numbered_list_item", ordered_match.group("text")))
            index += 1
            continue

        bullet_match = BULLETED_LIST.match(line)
        if bullet_match:
            flush_paragraph()
            blocks.append(_rich_text_block("bulleted_list_item", bullet_match.group("text")))
            index += 1
            continue

        paragraph_lines.append(line)
        index += 1

    if in_code:
        blocks.extend(_code_blocks("\n".join(code_lines), code_language))
    flush_paragraph()
    return blocks


__all__ = ["markdown_to_blocks", "rich_text_from_markdown"]
