from __future__ import annotations

from collections import defaultdict
import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

from .blocks import markdown_to_blocks
from .markdown import LocalAsset, PreparedMarkdownNote, iter_markdown_paths, prepare_markdown_note
from .notion import NotionApiError, build_file_block, extract_page_id


STATE_VERSION = 1
RENDERER_VERSION = 5
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ASSET_PROGRESS_EVERY = 5


@dataclass(frozen=True)
class ImportSummary:
    total_notes: int
    imported_notes: int = 0
    skipped_notes: int = 0
    failed_notes: int = 0
    created_folders: int = 0
    uploaded_assets: int = 0
    skipped_assets: int = 0
    unresolved_links: int = 0
    dry_run: bool = False


@dataclass(frozen=True)
class ImportedNoteReport:
    relative_path: str
    title: str
    status: str
    fingerprint: str
    page_id: str | None = None
    url: str | None = None
    asset_count: int = 0
    uploaded_assets: int = 0
    skipped_assets: int = 0
    unresolved_links: int = 0
    error: str | None = None


@dataclass(frozen=True)
class ImportResult:
    source_dir: Path
    parent_page_id: str
    state_path: Path
    report_path: Path
    summary: ImportSummary
    notes: tuple[ImportedNoteReport, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_dir": str(self.source_dir),
            "parent_page_id": self.parent_page_id,
            "state_path": str(self.state_path),
            "report_path": str(self.report_path),
            "summary": asdict(self.summary),
            "notes": [asdict(note) for note in self.notes],
        }


def _emit_progress(progress: Callable[[str], None] | None, note_label: str, detail: str | None = None) -> None:
    if progress is None:
        return
    progress(note_label if not detail else f"{note_label} - {detail}")


def _should_emit_asset_progress(asset_index: int, total_assets: int) -> bool:
    if total_assets <= 1:
        return True
    return (
        asset_index == 1
        or asset_index == total_assets
        or asset_index % ASSET_PROGRESS_EVERY == 0
    )


def _default_state_path(source_dir: Path) -> Path:
    return source_dir / "_wiz" / "notion_import_state.json"


def _default_report_path(source_dir: Path) -> Path:
    return source_dir / "_wiz" / "notion_import_report.json"


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "folders": {}, "notes": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "folders": {}, "notes": {}}
    if not isinstance(payload, dict):
        return {"version": STATE_VERSION, "folders": {}, "notes": {}}
    payload.setdefault("version", STATE_VERSION)
    payload.setdefault("folders", {})
    payload.setdefault("notes", {})
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_note_content_matches(entry: Any, prepared: PreparedMarkdownNote) -> bool:
    if not isinstance(entry, dict):
        return False
    return (
        bool(entry.get("page_id"))
        and entry.get("fingerprint") == prepared.fingerprint
        and int(entry.get("renderer_version") or 0) == RENDERER_VERSION
    )


def _state_assets_complete(entry: Any, prepared: PreparedMarkdownNote) -> bool:
    if not prepared.assets:
        return True
    if not isinstance(entry, dict):
        return False
    uploaded_assets = int(entry.get("uploaded_assets") or 0)
    skipped_assets = int(entry.get("skipped_assets") or 0)
    asset_count = len(prepared.assets)
    if uploaded_assets >= asset_count:
        return True
    return bool(entry.get("asset_upload_attempted")) and uploaded_assets + skipped_assets >= asset_count


def _state_note_matches(entry: Any, prepared: PreparedMarkdownNote, *, upload_assets: bool) -> bool:
    if not _state_note_content_matches(entry, prepared):
        return False
    if upload_assets:
        return _state_assets_complete(entry, prepared)
    return True


def _state_page_id(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    page_id = str(entry.get("page_id") or "").strip()
    return page_id or None


def _note_key(note: PreparedMarkdownNote) -> str:
    return note.relative_path.as_posix()


def _iter_child_pages(notion_client: Any, parent_page_id: str) -> Iterable[dict[str, Any]]:
    if hasattr(notion_client, "iter_block_children"):
        yield from notion_client.iter_block_children(parent_page_id)
        return
    if hasattr(notion_client, "list_block_children"):
        start_cursor: str | None = None
        while True:
            response = notion_client.list_block_children(parent_page_id, start_cursor=start_cursor, page_size=100)
            for item in response.get("results", []):
                if isinstance(item, dict):
                    yield item
            if not response.get("has_more"):
                return
            next_cursor = response.get("next_cursor")
            start_cursor = str(next_cursor) if next_cursor else None
            if not start_cursor:
                return


def _child_page_index(
    *,
    parent_page_id: str,
    notion_client: Any,
    child_page_cache: dict[str, dict[str, list[str]]],
) -> dict[str, list[str]]:
    cached = child_page_cache.get(parent_page_id)
    if cached is not None:
        return cached

    by_title: dict[str, list[str]] = defaultdict(list)
    for item in _iter_child_pages(notion_client, parent_page_id):
        if item.get("type") != "child_page" or item.get("in_trash"):
            continue
        title = str(item.get("child_page", {}).get("title") or "").strip()
        page_id = str(item.get("id") or "").strip()
        if title and page_id:
            by_title[title].append(page_id)
    child_page_cache[parent_page_id] = dict(by_title)
    return child_page_cache[parent_page_id]


def _cache_child_page(
    *,
    parent_page_id: str,
    title: str,
    page_id: str,
    child_page_cache: dict[str, dict[str, list[str]]],
) -> None:
    by_title = child_page_cache.setdefault(parent_page_id, {})
    pages = by_title.setdefault(title, [])
    if page_id not in pages:
        pages.append(page_id)


def _unique_child_page_id(
    *,
    parent_page_id: str,
    title: str,
    notion_client: Any,
    child_page_cache: dict[str, dict[str, list[str]]],
) -> str | None:
    matches = _child_page_index(
        parent_page_id=parent_page_id,
        notion_client=notion_client,
        child_page_cache=child_page_cache,
    ).get(title, [])
    if len(matches) != 1:
        return None
    return matches[0]


def _ensure_folder_pages(
    *,
    note: PreparedMarkdownNote,
    root_parent_page_id: str,
    notion_client: Any,
    state: dict[str, Any],
    resume: bool,
    dry_run: bool,
    child_page_cache: dict[str, dict[str, list[str]]],
    progress: Callable[[str], None] | None = None,
    note_label: str = "",
) -> tuple[str, int, bool]:
    parent_page_id = root_parent_page_id
    created = 0
    state_changed = False
    folder_parts = note.relative_path.parent.parts
    accumulated: list[str] = []
    folders = state.setdefault("folders", {})

    for folder_name in folder_parts:
        accumulated.append(folder_name)
        folder_key = "/".join(accumulated)
        existing_page_id = _state_page_id(folders.get(folder_key)) if resume else None
        if existing_page_id:
            parent_page_id = existing_page_id
            continue

        if dry_run:
            created += 1
            parent_page_id = f"dry-run-folder:{folder_key}"
            continue

        discovered_page_id = _unique_child_page_id(
            parent_page_id=parent_page_id,
            title=folder_name,
            notion_client=notion_client,
            child_page_cache=child_page_cache,
        )
        if discovered_page_id:
            parent_page_id = discovered_page_id
            folders[folder_key] = {
                "page_id": parent_page_id,
                "title": folder_name,
                "discovered_at": _now_iso(),
            }
            state_changed = True
            _emit_progress(progress, note_label, f"reusing existing folder page: {folder_name}")
            continue

        created += 1
        _emit_progress(progress, note_label, f"creating folder page: {folder_name}")
        folder_parent_id = parent_page_id
        response = notion_client.create_page(parent_page_id=folder_parent_id, title=folder_name, markdown=None)
        parent_page_id = str(response["id"])
        _cache_child_page(
            parent_page_id=folder_parent_id,
            title=folder_name,
            page_id=parent_page_id,
            child_page_cache=child_page_cache,
        )
        folders[folder_key] = {
            "page_id": parent_page_id,
            "title": folder_name,
            "created_at": _now_iso(),
        }
        state_changed = True
    return parent_page_id, created, state_changed


def _is_markdown_parse_error(error: NotionApiError) -> bool:
    return (
        error.status_code == 400
        and error.code == "validation_error"
        and "markdown" in error.message.lower()
    )


def _is_archived_block_error(error: NotionApiError) -> bool:
    return (
        error.status_code == 400
        and error.code == "validation_error"
        and "archived" in error.message.lower()
    )


def _rich_text_content(block: dict[str, Any]) -> str:
    block_type = str(block.get("type") or "")
    payload = block.get(block_type)
    if not isinstance(payload, dict):
        return ""
    rich_text = payload.get("rich_text")
    if not isinstance(rich_text, list):
        return ""
    parts: list[str] = []
    for item in rich_text:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, dict):
            parts.append(str(text.get("content") or ""))
    return "".join(parts).strip()


def _asset_placeholder_texts(asset: LocalAsset) -> set[str]:
    placeholder = asset.placeholder.strip()
    texts = {placeholder}
    if placeholder.startswith("[") and placeholder.endswith("]"):
        texts.add(placeholder[1:-1])
    return texts


def _upload_asset_block(
    *,
    asset: LocalAsset,
    notion_client: Any,
    max_upload_bytes: int,
) -> dict[str, Any] | None:
    try:
        size = asset.path.stat().st_size
    except OSError:
        return None
    if size > max_upload_bytes:
        return None
    upload_id = notion_client.upload_file(asset.path)
    caption = "" if asset.kind == "image" else asset.path.name
    return build_file_block(upload_id, asset.path, caption=caption)


def _is_skippable_asset_upload_error(error: NotionApiError) -> bool:
    if error.status_code != 400 or error.code != "validation_error":
        return False
    message = error.message.lower()
    return (
        "extension that is not supported for the file upload api" in message
        or ("file size of" in message and "exceeds the limit" in message)
    )


def _blocks_with_uploaded_assets(
    *,
    blocks: list[dict[str, Any]],
    note: PreparedMarkdownNote,
    notion_client: Any,
    max_upload_bytes: int,
    progress: Callable[[str], None] | None = None,
    note_label: str = "",
) -> tuple[list[dict[str, Any]], int, int]:
    uploaded_blocks: dict[LocalAsset, dict[str, Any]] = {}
    skipped_assets: set[LocalAsset] = set()
    placed_assets: set[LocalAsset] = set()
    uploaded = 0
    skipped = 0

    def ensure_uploaded(asset: LocalAsset) -> dict[str, Any] | None:
        nonlocal uploaded, skipped
        if asset in uploaded_blocks:
            return copy.deepcopy(uploaded_blocks[asset])
        if asset in skipped_assets:
            return None

        asset_index = uploaded + skipped + 1
        if _should_emit_asset_progress(asset_index, len(note.assets)):
            _emit_progress(
                progress,
                note_label,
                f"uploading asset {asset_index}/{len(note.assets)}: {asset.path.name}",
            )
        try:
            asset_block = _upload_asset_block(
                asset=asset,
                notion_client=notion_client,
                max_upload_bytes=max_upload_bytes,
            )
        except NotionApiError as exc:
            if not _is_skippable_asset_upload_error(exc):
                raise
            asset_block = None
        if asset_block is None:
            skipped_assets.add(asset)
            skipped += 1
            _emit_progress(
                progress,
                note_label,
                f"skipped asset {asset_index}/{len(note.assets)}: {asset.path.name}",
            )
            return None

        uploaded_blocks[asset] = asset_block
        uploaded += 1
        return copy.deepcopy(asset_block)

    placeholder_to_asset: dict[str, LocalAsset] = {}
    for asset in note.assets:
        for placeholder_text in _asset_placeholder_texts(asset):
            placeholder_to_asset.setdefault(placeholder_text, asset)

    placed_blocks: list[dict[str, Any]] = []
    for block in blocks:
        asset = placeholder_to_asset.get(_rich_text_content(block))
        if asset is None:
            placed_blocks.append(block)
            continue

        asset_block = ensure_uploaded(asset)
        if asset_block is None:
            placed_blocks.append(block)
            continue

        placed_assets.add(asset)
        placed_blocks.append(asset_block)

    for asset in note.assets:
        if asset in placed_assets:
            continue
        asset_block = ensure_uploaded(asset)
        if asset_block is not None:
            placed_blocks.append(asset_block)

    return placed_blocks, uploaded, skipped


def _create_note_page(
    *,
    notion_client: Any,
    parent_page_id: str,
    note: PreparedMarkdownNote,
    upload_assets: bool,
    max_upload_bytes: int,
    on_page_created: Callable[[dict[str, Any]], None] | None = None,
    progress: Callable[[str], None] | None = None,
    note_label: str = "",
) -> tuple[dict[str, Any], str, int, int]:
    if upload_assets and note.assets:
        _emit_progress(progress, note_label, "creating page")
        page = notion_client.create_page(parent_page_id=parent_page_id, title=note.title, markdown=None)
        if on_page_created is not None:
            on_page_created(page)
        _emit_progress(progress, note_label, "rendering blocks")
        blocks = markdown_to_blocks(note.markdown)
        blocks, uploaded, skipped = _blocks_with_uploaded_assets(
            blocks=blocks,
            note=note,
            notion_client=notion_client,
            max_upload_bytes=max_upload_bytes,
            progress=progress,
            note_label=note_label,
        )
        if blocks:
            _emit_progress(progress, note_label, f"appending {len(blocks)} blocks")
            notion_client.append_blocks(str(page["id"]), blocks)
        return page, "imported_with_blocks", uploaded, skipped

    try:
        _emit_progress(progress, note_label, "creating page from markdown")
        return (
            notion_client.create_page(
                parent_page_id=parent_page_id,
                title=note.title,
                markdown=note.markdown,
            ),
            "imported",
            0,
            0,
        )
    except NotionApiError as exc:
        if not _is_markdown_parse_error(exc):
            raise

    _emit_progress(progress, note_label, "markdown rejected, falling back to blocks")
    page = notion_client.create_page(parent_page_id=parent_page_id, title=note.title, markdown=None)
    if on_page_created is not None:
        on_page_created(page)
    blocks = markdown_to_blocks(note.markdown)
    if blocks:
        _emit_progress(progress, note_label, f"appending {len(blocks)} blocks")
        notion_client.append_blocks(str(page["id"]), blocks)
    return page, "imported_with_blocks", 0, 0


def _update_note_page(
    *,
    notion_client: Any,
    page_id: str,
    note: PreparedMarkdownNote,
    upload_assets: bool,
    max_upload_bytes: int,
    progress: Callable[[str], None] | None = None,
    note_label: str = "",
) -> tuple[dict[str, Any], str, int, int]:
    if upload_assets and note.assets:
        _emit_progress(progress, note_label, "updating page and clearing existing content")
        page = notion_client.update_page(page_id=page_id, title=note.title, erase_content=True)
        _emit_progress(progress, note_label, "rendering blocks")
        blocks = markdown_to_blocks(note.markdown)
        blocks, uploaded, skipped = _blocks_with_uploaded_assets(
            blocks=blocks,
            note=note,
            notion_client=notion_client,
            max_upload_bytes=max_upload_bytes,
            progress=progress,
            note_label=note_label,
        )
        if blocks:
            _emit_progress(progress, note_label, f"appending {len(blocks)} blocks")
            notion_client.append_blocks(page_id, blocks)
        return page, "updated_with_blocks", uploaded, skipped

    _emit_progress(progress, note_label, "updating page title")
    page = notion_client.update_page(page_id=page_id, title=note.title)
    try:
        _emit_progress(progress, note_label, "replacing page markdown")
        notion_client.update_page_markdown(page_id=page_id, markdown=note.markdown)
        return page, "updated", 0, 0
    except NotionApiError as exc:
        if not _is_markdown_parse_error(exc):
            raise

    _emit_progress(progress, note_label, "markdown rejected, replacing content with blocks")
    page = notion_client.update_page(page_id=page_id, title=note.title, erase_content=True)
    blocks = markdown_to_blocks(note.markdown)
    if blocks:
        _emit_progress(progress, note_label, f"appending {len(blocks)} blocks")
        notion_client.append_blocks(page_id, blocks)
    return page, "updated_with_blocks", 0, 0


def _clear_folder_state_for_note(*, state: dict[str, Any], note: PreparedMarkdownNote) -> None:
    folders = state.setdefault("folders", {})
    accumulated: list[str] = []
    for folder_name in note.relative_path.parent.parts:
        accumulated.append(folder_name)
        folders.pop("/".join(accumulated), None)


def _write_partial_note_state(
    *,
    state: dict[str, Any],
    state_path: Path,
    note_key: str,
    note: PreparedMarkdownNote,
    page: dict[str, Any],
    upload_assets: bool,
) -> None:
    page_id = str(page.get("id") or "").strip()
    if not page_id:
        return
    page_url = str(page.get("url") or "")
    state.setdefault("notes", {})[note_key] = {
        "page_id": page_id,
        "url": page_url,
        "title": note.title,
        "fingerprint": None,
        "renderer_version": 0,
        "asset_count": len(note.assets),
        "uploaded_assets": 0,
        "skipped_assets": 0,
        "asset_upload_attempted": bool(upload_assets),
        "import_started_at": _now_iso(),
    }
    _write_json(state_path, state)


def import_markdown_tree(
    *,
    source_dir: Path,
    parent_page_id: str,
    notion_client: Any,
    state_path: Path | None = None,
    report_path: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    resume: bool = True,
    upload_assets: bool = True,
    include_wiz_meta: bool = False,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    progress: Callable[[str], None] | None = None,
    stop_on_error: bool = False,
) -> ImportResult:
    source_dir = source_dir.resolve()
    parent_page_id = extract_page_id(parent_page_id)
    state_path = state_path or _default_state_path(source_dir)
    report_path = report_path or _default_report_path(source_dir)
    state = _load_state(state_path)
    paths = iter_markdown_paths(source_dir, include_wiz_meta=include_wiz_meta, limit=limit)
    reports: list[ImportedNoteReport] = []
    child_page_cache: dict[str, dict[str, list[str]]] = {}

    imported_notes = 0
    skipped_notes = 0
    failed_notes = 0
    created_folders = 0
    uploaded_assets = 0
    skipped_assets = 0
    unresolved_links = 0

    total = len(paths)
    for index, path in enumerate(paths, start=1):
        prepared = prepare_markdown_note(path, source_dir)
        key = _note_key(prepared)
        note_label = f"{index}/{total} {key}"
        unresolved_links += len(prepared.unresolved_links)
        _emit_progress(progress, note_label)

        state_entry = state.setdefault("notes", {}).get(key)
        if resume and _state_note_matches(state_entry, prepared, upload_assets=upload_assets):
            _emit_progress(progress, note_label, "skipped (already imported)")
            skipped_notes += 1
            reports.append(
                ImportedNoteReport(
                    relative_path=key,
                    title=prepared.title,
                    status="skipped",
                    fingerprint=prepared.fingerprint,
                    page_id=_state_page_id(state_entry),
                    asset_count=len(prepared.assets),
                    unresolved_links=len(prepared.unresolved_links),
                )
            )
            continue

        existing_page_id = _state_page_id(state_entry) if resume else None
        folder_parent_id, folder_created, folder_state_changed = _ensure_folder_pages(
            note=prepared,
            root_parent_page_id=parent_page_id,
            notion_client=notion_client,
            state=state,
            resume=resume,
            dry_run=dry_run,
            child_page_cache=child_page_cache,
            progress=progress,
            note_label=note_label,
        )
        created_folders += folder_created
        if (folder_created or folder_state_changed) and not dry_run:
            _write_json(state_path, state)

        if resume and not dry_run and existing_page_id is None:
            existing_page_id = _unique_child_page_id(
                parent_page_id=folder_parent_id,
                title=prepared.title,
                notion_client=notion_client,
                child_page_cache=child_page_cache,
            )
            if existing_page_id:
                _emit_progress(progress, note_label, "reusing existing page discovered in Notion")

        if dry_run:
            _emit_progress(progress, note_label, "dry run complete")
            reports.append(
                ImportedNoteReport(
                    relative_path=key,
                    title=prepared.title,
                    status="would_import",
                    fingerprint=prepared.fingerprint,
                    page_id=None,
                    asset_count=len(prepared.assets),
                    unresolved_links=len(prepared.unresolved_links),
                )
            )
            continue

        try:
            try:
                def on_page_created(page: dict[str, Any]) -> None:
                    _cache_child_page(
                        parent_page_id=folder_parent_id,
                        title=prepared.title,
                        page_id=str(page.get("id") or ""),
                        child_page_cache=child_page_cache,
                    )
                    _write_partial_note_state(
                        state=state,
                        state_path=state_path,
                        note_key=key,
                        note=prepared,
                        page=page,
                        upload_assets=upload_assets,
                    )

                if existing_page_id:
                    page, note_status, note_uploaded_assets, note_skipped_assets = _update_note_page(
                        notion_client=notion_client,
                        page_id=existing_page_id,
                        note=prepared,
                        upload_assets=upload_assets,
                        max_upload_bytes=max_upload_bytes,
                        progress=progress,
                        note_label=note_label,
                    )
                else:
                    page, note_status, note_uploaded_assets, note_skipped_assets = _create_note_page(
                        notion_client=notion_client,
                        parent_page_id=folder_parent_id,
                        note=prepared,
                        upload_assets=upload_assets,
                        max_upload_bytes=max_upload_bytes,
                        on_page_created=on_page_created,
                        progress=progress,
                        note_label=note_label,
                    )
            except NotionApiError as exc:
                if existing_page_id:
                    raise
                if not (resume and prepared.relative_path.parent.parts and _is_archived_block_error(exc)):
                    raise
                _emit_progress(progress, note_label, "folder page archived, recreating folder path")
                _clear_folder_state_for_note(state=state, note=prepared)
                child_page_cache.clear()
                folder_parent_id, recreated_folders, recreated_folder_state = _ensure_folder_pages(
                    note=prepared,
                    root_parent_page_id=parent_page_id,
                    notion_client=notion_client,
                    state=state,
                    resume=resume,
                    dry_run=dry_run,
                    child_page_cache=child_page_cache,
                    progress=progress,
                    note_label=note_label,
                )
                created_folders += recreated_folders
                if recreated_folders or recreated_folder_state:
                    _write_json(state_path, state)
                page, note_status, note_uploaded_assets, note_skipped_assets = _create_note_page(
                    notion_client=notion_client,
                    parent_page_id=folder_parent_id,
                    note=prepared,
                    upload_assets=upload_assets,
                    max_upload_bytes=max_upload_bytes,
                    on_page_created=on_page_created,
                    progress=progress,
                    note_label=note_label,
                )
            page_id = str(page["id"])
            page_url = str(page.get("url") or "")
            if not upload_assets:
                note_skipped_assets = len(prepared.assets)

            state.setdefault("notes", {})[key] = {
                "page_id": page_id,
                "url": page_url,
                "title": prepared.title,
                "fingerprint": prepared.fingerprint,
                "renderer_version": RENDERER_VERSION,
                "asset_count": len(prepared.assets),
                "uploaded_assets": note_uploaded_assets,
                "skipped_assets": note_skipped_assets,
                "asset_upload_attempted": bool(upload_assets),
                "imported_at": _now_iso(),
            }
            _write_json(state_path, state)
            _emit_progress(progress, note_label, "done")

            imported_notes += 1
            uploaded_assets += note_uploaded_assets
            skipped_assets += note_skipped_assets
            reports.append(
                ImportedNoteReport(
                    relative_path=key,
                    title=prepared.title,
                    status=note_status,
                    fingerprint=prepared.fingerprint,
                    page_id=page_id,
                    url=page_url or None,
                    asset_count=len(prepared.assets),
                    uploaded_assets=note_uploaded_assets,
                    skipped_assets=note_skipped_assets,
                    unresolved_links=len(prepared.unresolved_links),
                )
            )
        except Exception as exc:
            _emit_progress(progress, note_label, f"failed: {exc}")
            failed_notes += 1
            reports.append(
                ImportedNoteReport(
                    relative_path=key,
                    title=prepared.title,
                    status="failed",
                    fingerprint=prepared.fingerprint,
                    asset_count=len(prepared.assets),
                    unresolved_links=len(prepared.unresolved_links),
                    error=str(exc),
                )
            )
            if stop_on_error:
                break

    summary = ImportSummary(
        total_notes=total,
        imported_notes=imported_notes,
        skipped_notes=skipped_notes,
        failed_notes=failed_notes,
        created_folders=created_folders,
        uploaded_assets=uploaded_assets,
        skipped_assets=skipped_assets,
        unresolved_links=unresolved_links,
        dry_run=dry_run,
    )
    result = ImportResult(
        source_dir=source_dir,
        parent_page_id=parent_page_id,
        state_path=state_path,
        report_path=report_path,
        summary=summary,
        notes=tuple(reports),
    )
    if not dry_run:
        _write_json(report_path, result.to_dict())
    return result


def scan_markdown_tree(
    *,
    source_dir: Path,
    limit: int | None = None,
    include_wiz_meta: bool = False,
) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    paths = iter_markdown_paths(source_dir, include_wiz_meta=include_wiz_meta, limit=limit)
    total_assets = 0
    unresolved = 0
    notes: list[dict[str, Any]] = []
    for path in paths:
        prepared = prepare_markdown_note(path, source_dir)
        total_assets += len(prepared.assets)
        unresolved += len(prepared.unresolved_links)
        notes.append(
            {
                "relative_path": prepared.relative_path.as_posix(),
                "title": prepared.title,
                "assets": len(prepared.assets),
                "unresolved_links": len(prepared.unresolved_links),
            }
        )
    return {
        "source_dir": str(source_dir),
        "summary": {
            "total_notes": len(paths),
            "local_assets": total_assets,
            "unresolved_links": unresolved,
        },
        "notes": notes,
    }


__all__ = [
    "ImportResult",
    "ImportSummary",
    "ImportedNoteReport",
    "import_markdown_tree",
    "scan_markdown_tree",
]
