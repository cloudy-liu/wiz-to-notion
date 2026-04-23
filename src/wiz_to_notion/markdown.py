from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import unquote, urlparse


FRONTMATTER_BLOCK = re.compile(r"\A---\s*\r?\n(?P<body>.*?)(?:\r?\n)---(?:\r?\n|\Z)", re.DOTALL)
FRONTMATTER_FIELD = re.compile(r"^(?P<key>[A-Za-z0-9_]+):\s*(?P<value>.*)$")
FIRST_HEADING = re.compile(r"^\s*#\s+(?P<title>.+?)\s*$", re.MULTILINE)
MARKDOWN_LINK = re.compile(r"(?P<bang>!)?\[(?P<label>[^\]]*)\]\((?P<target>[^)\r\n]*)\)")
LINKED_MARKDOWN_IMAGE = re.compile(
    r"\[!\[(?P<alt>[^\]]*)\]\((?P<image_target>[^)\r\n]*)\)\]\((?P<link_target>[^)\r\n]*)\)"
)
GREEDY_EXTERNAL_MARKDOWN_LINK = re.compile(
    r"(?P<bang>!)?\[(?P<label>[^\]]*)\]\((?P<target>https?://[^\r\n]+)\)",
    re.IGNORECASE,
)
HTML_IMAGE_TAG = re.compile(r"<img\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
HTML_LINK_TAG = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
HTML_ATTR = re.compile(
    r"""\b(?P<name>src|href)\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s>]+))""",
    re.IGNORECASE | re.DOTALL,
)
TITLE_SUFFIX = re.compile(r"\.(?:md|markdown|html?)$", re.IGNORECASE)
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:/")
MAX_NOTION_MARKDOWN_LINE_LENGTH = 1800
MAX_NOTION_LINK_TARGET_LENGTH = 1800

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


@dataclass(frozen=True)
class LocalAsset:
    original_target: str
    path: Path
    kind: str
    placeholder: str


@dataclass(frozen=True)
class UnresolvedLink:
    target: str
    source: str


@dataclass(frozen=True)
class PreparedMarkdownNote:
    path: Path
    relative_path: Path
    title: str
    markdown: str
    frontmatter: dict[str, str]
    assets: tuple[LocalAsset, ...]
    unresolved_links: tuple[UnresolvedLink, ...]
    fingerprint: str


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_BLOCK.match(text)
    if not match:
        return {}, text.strip()

    fields: dict[str, str] = {}
    for raw_line in match.group("body").splitlines():
        field_match = FRONTMATTER_FIELD.match(raw_line.strip())
        if field_match is None:
            continue
        key = field_match.group("key").strip()
        value = _unquote_yaml_scalar(field_match.group("value").strip())
        fields[key] = value
    return fields, text[match.end() :].strip()


def _unquote_yaml_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
    return value


def _title_from_markdown(markdown: str) -> str | None:
    match = FIRST_HEADING.search(markdown)
    if match is None:
        return None
    title = match.group("title").strip()
    return title or None


def _clean_title(value: str) -> str:
    value = TITLE_SUFFIX.sub("", value.strip())
    value = re.sub(r"\s+", " ", value).strip()
    return value[:2000] if value else "Untitled"


def _normalize_markdown_target(target: str) -> str:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target:
        return ""
    if " " in target and not Path(target).exists():
        # Strip optional Markdown link title: [x](file.png "title")
        target = target.split(None, 1)[0].strip()
    return unquote(target.replace("\\", "/"))


def _is_external_or_anchor(target: str) -> bool:
    if not target or target.startswith("#"):
        return True
    if WINDOWS_ABSOLUTE_PATH.match(target):
        return False
    parsed = urlparse(target)
    return bool(parsed.scheme and parsed.scheme.lower() not in {"."})


def _resolve_local_target(note_path: Path, source_dir: Path, target: str) -> Path | None:
    normalized = _normalize_markdown_target(target)
    if _is_external_or_anchor(normalized):
        return None

    candidate = Path(normalized)
    if not candidate.is_absolute():
        candidate = note_path.parent / normalized
    try:
        resolved = candidate.resolve(strict=False)
        source_root = source_dir.resolve(strict=False)
    except OSError:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    try:
        resolved.relative_to(source_root)
    except ValueError:
        return None
    return resolved


def _asset_kind(path: Path, *, is_image_reference: bool = False) -> str:
    if is_image_reference or path.suffix.lower() in IMAGE_EXTENSIONS:
        return "image"
    return "attachment"


def _placeholder_for(path: Path, kind: str) -> str:
    label = "Image" if kind == "image" else "Attachment"
    return f"[{label} imported below: {path.name}]"


def _extract_attr(attrs_text: str, attr_name: str) -> str:
    for match in HTML_ATTR.finditer(attrs_text):
        if match.group("name").lower() != attr_name:
            continue
        return next(value for value in match.group("double", "single", "bare") if value is not None)
    return ""


def _wrap_text(value: str, *, max_chars: int = MAX_NOTION_MARKDOWN_LINE_LENGTH) -> list[str]:
    chunks: list[str] = []
    cursor = 0
    while cursor < len(value):
        end = min(len(value), cursor + max_chars)
        if end < len(value):
            split_at = max(
                value.rfind(" ", cursor, end),
                value.rfind("/", cursor, end),
                value.rfind("&", cursor, end),
                value.rfind("%0A", cursor, end),
            )
            if split_at > cursor + max_chars // 2:
                end = split_at + 1
        chunk = value[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        cursor = end
    return chunks


def _rewrite_oversized_external_links(markdown: str) -> str:
    def replace_link(match: re.Match[str]) -> str:
        raw_target = match.group("target") or ""
        target = _normalize_markdown_target(raw_target)
        if not target or target.startswith("#") or not _is_external_or_anchor(target):
            return match.group(0)
        if len(target) <= MAX_NOTION_LINK_TARGET_LENGTH:
            return match.group(0)

        label = (match.group("label") or "").strip() or "Long link"
        wrapped_target = "\n".join(_wrap_text(target))
        return f"{label}\n\nLong link target:\n{wrapped_target}"

    markdown = MARKDOWN_LINK.sub(replace_link, markdown)

    def replace_greedy_link(match: re.Match[str]) -> str:
        target = (match.group("target") or "").strip()
        if len(target) <= MAX_NOTION_LINK_TARGET_LENGTH:
            return match.group(0)
        label = (match.group("label") or "").strip() or "Long link"
        wrapped_target = "\n".join(_wrap_text(target))
        return f"{label}\n\nLong link target:\n{wrapped_target}"

    return GREEDY_EXTERNAL_MARKDOWN_LINK.sub(replace_greedy_link, markdown)


def _wrap_oversized_lines(markdown: str) -> str:
    wrapped_lines: list[str] = []
    for line in markdown.splitlines():
        if len(line) <= MAX_NOTION_MARKDOWN_LINE_LENGTH:
            wrapped_lines.append(line)
            continue
        wrapped_lines.extend(_wrap_text(line))
    return "\n".join(wrapped_lines)


def _dedupe_assets(assets: Iterable[LocalAsset]) -> tuple[LocalAsset, ...]:
    seen: set[Path] = set()
    deduped: list[LocalAsset] = []
    for asset in assets:
        if asset.path in seen:
            continue
        seen.add(asset.path)
        deduped.append(asset)
    return tuple(deduped)


def _fingerprint(markdown: str, assets: tuple[LocalAsset, ...]) -> str:
    digest = sha256()
    digest.update(markdown.encode("utf-8"))
    for asset in sorted(assets, key=lambda item: str(item.path).lower()):
        try:
            stat = asset.path.stat()
        except OSError:
            continue
        digest.update(str(asset.path).encode("utf-8", errors="ignore"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


def prepare_markdown_note(path: Path, source_dir: Path) -> PreparedMarkdownNote:
    path = path.resolve()
    source_dir = source_dir.resolve()
    original_text = path.read_text(encoding="utf-8-sig")
    frontmatter, markdown = parse_frontmatter(original_text)
    assets: list[LocalAsset] = []
    unresolved: list[UnresolvedLink] = []

    def replace_markdown_link(match: re.Match[str]) -> str:
        raw_target = match.group("target") or ""
        normalized_target = _normalize_markdown_target(raw_target)
        is_image = bool(match.group("bang"))
        local_path = _resolve_local_target(path, source_dir, raw_target)
        if local_path is None:
            if normalized_target and not _is_external_or_anchor(normalized_target):
                unresolved.append(UnresolvedLink(target=normalized_target, source="markdown"))
            return match.group(0)
        if local_path.suffix.lower() == ".md":
            unresolved.append(UnresolvedLink(target=normalized_target, source="markdown"))
            return match.group(0)
        kind = _asset_kind(local_path, is_image_reference=is_image)
        placeholder = _placeholder_for(local_path, kind)
        assets.append(LocalAsset(raw_target, local_path, kind, placeholder))
        return placeholder

    def replace_linked_markdown_image(match: re.Match[str]) -> str:
        raw_image_target = match.group("image_target") or ""
        normalized_image_target = _normalize_markdown_target(raw_image_target)
        image_path = _resolve_local_target(path, source_dir, raw_image_target)
        if image_path is None:
            if normalized_image_target and not _is_external_or_anchor(normalized_image_target):
                unresolved.append(UnresolvedLink(target=normalized_image_target, source="markdown"))
            return match.group(0)

        kind = _asset_kind(image_path, is_image_reference=True)
        placeholder = _placeholder_for(image_path, kind)
        assets.append(LocalAsset(raw_image_target, image_path, kind, placeholder))

        raw_link_target = match.group("link_target") or ""
        normalized_link_target = _normalize_markdown_target(raw_link_target)
        if not normalized_link_target:
            return placeholder
        if not _is_external_or_anchor(normalized_link_target):
            unresolved.append(UnresolvedLink(target=normalized_link_target, source="markdown"))
            return placeholder
        return f"{placeholder}({raw_link_target.strip()})"

    markdown = LINKED_MARKDOWN_IMAGE.sub(replace_linked_markdown_image, markdown)
    markdown = MARKDOWN_LINK.sub(replace_markdown_link, markdown)

    def replace_html_image(match: re.Match[str]) -> str:
        src = _extract_attr(match.group("attrs") or "", "src")
        normalized_src = _normalize_markdown_target(src)
        local_path = _resolve_local_target(path, source_dir, src)
        if local_path is None:
            if normalized_src and not _is_external_or_anchor(normalized_src):
                unresolved.append(UnresolvedLink(target=normalized_src, source="html"))
            return match.group(0)
        kind = _asset_kind(local_path, is_image_reference=True)
        placeholder = _placeholder_for(local_path, kind)
        assets.append(LocalAsset(src, local_path, kind, placeholder))
        return placeholder

    markdown = HTML_IMAGE_TAG.sub(replace_html_image, markdown)

    def replace_html_link(match: re.Match[str]) -> str:
        href = _extract_attr(match.group("attrs") or "", "href")
        normalized_href = _normalize_markdown_target(href)
        local_path = _resolve_local_target(path, source_dir, href)
        if local_path is None:
            if normalized_href and not _is_external_or_anchor(normalized_href):
                unresolved.append(UnresolvedLink(target=normalized_href, source="html"))
            return match.group(0)
        if local_path.suffix.lower() == ".md":
            unresolved.append(UnresolvedLink(target=normalized_href, source="html"))
            return match.group(0)
        kind = _asset_kind(local_path)
        placeholder = _placeholder_for(local_path, kind)
        assets.append(LocalAsset(href, local_path, kind, placeholder))
        return placeholder

    markdown = HTML_LINK_TAG.sub(replace_html_link, markdown)
    markdown = _rewrite_oversized_external_links(markdown)
    markdown = _wrap_oversized_lines(markdown)
    assets_tuple = _dedupe_assets(assets)
    title = frontmatter.get("title") or _title_from_markdown(markdown) or path.stem
    relative_path = path.relative_to(source_dir)
    return PreparedMarkdownNote(
        path=path,
        relative_path=relative_path,
        title=_clean_title(title),
        markdown=markdown.strip(),
        frontmatter=frontmatter,
        assets=assets_tuple,
        unresolved_links=tuple(unresolved),
        fingerprint=_fingerprint(markdown.strip(), assets_tuple),
    )


def iter_markdown_paths(source_dir: Path, *, include_wiz_meta: bool = False, limit: int | None = None) -> tuple[Path, ...]:
    source_dir = source_dir.resolve()
    paths: list[Path] = []
    for path in sorted(source_dir.rglob("*.md"), key=lambda item: item.relative_to(source_dir).as_posix().lower()):
        relative_parts = path.relative_to(source_dir).parts
        if not include_wiz_meta and "_wiz" in relative_parts:
            continue
        paths.append(path)
        if limit is not None and len(paths) >= limit:
            break
    return tuple(paths)


__all__ = [
    "LocalAsset",
    "PreparedMarkdownNote",
    "UnresolvedLink",
    "iter_markdown_paths",
    "parse_frontmatter",
    "prepare_markdown_note",
]
