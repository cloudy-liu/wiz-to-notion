from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Callable, Iterable

from .markdown import iter_markdown_paths, prepare_markdown_note
from .notion import extract_page_id


@dataclass(frozen=True)
class ExistingChildPage:
    page_id: str
    title: str
    parent_path: str
    path: str
    created_time: str = ""
    last_edited_time: str = ""


@dataclass(frozen=True)
class DuplicateCleanupGroup:
    parent_path: str
    title: str
    expected_count: int
    actual_count: int
    redundant_count: int
    keep_ids: tuple[str, ...]
    remove_ids: tuple[str, ...]


@dataclass(frozen=True)
class DuplicateCleanupPlan:
    source_dir: Path
    root_page_id: str
    total_child_pages: int
    groups: tuple[DuplicateCleanupGroup, ...]
    dry_run: bool = True

    @property
    def page_ids_to_trash(self) -> tuple[str, ...]:
        return tuple(page_id for group in self.groups for page_id in group.remove_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_dir": str(self.source_dir),
            "root_page_id": self.root_page_id,
            "total_child_pages": self.total_child_pages,
            "duplicate_groups": [asdict(group) for group in self.groups],
            "page_ids_to_trash": list(self.page_ids_to_trash),
            "dry_run": self.dry_run,
        }


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"folders": {}, "notes": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"folders": {}, "notes": {}}
    if not isinstance(payload, dict):
        return {"folders": {}, "notes": {}}
    payload.setdefault("folders", {})
    payload.setdefault("notes", {})
    return payload


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


def build_expected_child_page_counts(source_dir: Path, *, include_wiz_meta: bool = False) -> dict[tuple[str, str], int]:
    source_dir = source_dir.resolve()
    counts: dict[tuple[str, str], int] = defaultdict(int)
    seen_folder_edges: set[tuple[str, str]] = set()

    for path in iter_markdown_paths(source_dir, include_wiz_meta=include_wiz_meta):
        prepared = prepare_markdown_note(path, source_dir)
        accumulated: list[str] = []
        for folder_name in prepared.relative_path.parent.parts:
            parent_path = "/".join(accumulated)
            edge = (parent_path, folder_name)
            if edge not in seen_folder_edges:
                counts[edge] += 1
                seen_folder_edges.add(edge)
            accumulated.append(folder_name)

        note_parent = prepared.relative_path.parent.as_posix()
        counts[(note_parent, prepared.title)] += 1

    return dict(counts)


def build_state_child_page_groups(state: dict[str, Any]) -> dict[tuple[str, str], tuple[str, ...]]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)

    folders = state.get("folders")
    if isinstance(folders, dict):
        for folder_key, meta in folders.items():
            if not isinstance(meta, dict):
                continue
            page_id = str(meta.get("page_id") or "").strip()
            if not page_id:
                continue
            folder_path = Path(str(folder_key))
            title = folder_path.name
            parent_path = folder_path.parent.as_posix()
            groups[(parent_path if parent_path != "." else "", title)].append(page_id)

    notes = state.get("notes")
    if isinstance(notes, dict):
        for relative_path, meta in notes.items():
            if not isinstance(meta, dict):
                continue
            page_id = str(meta.get("page_id") or "").strip()
            title = str(meta.get("title") or "").strip()
            if not page_id or not title:
                continue
            note_path = Path(str(relative_path))
            parent_path = note_path.parent.as_posix()
            groups[(parent_path if parent_path != "." else "", title)].append(page_id)

    return {key: tuple(value) for key, value in groups.items()}


def _child_page_sort_key(page: ExistingChildPage) -> tuple[str, str, str]:
    return (
        page.last_edited_time or "",
        page.created_time or "",
        page.page_id,
    )


def plan_duplicate_cleanup(
    *,
    expected_counts: dict[tuple[str, str], int],
    state_page_groups: dict[tuple[str, str], tuple[str, ...]],
    actual_pages: Iterable[ExistingChildPage],
) -> DuplicateCleanupPlan:
    actual_pages = tuple(actual_pages)
    groups_by_key: dict[tuple[str, str], list[ExistingChildPage]] = defaultdict(list)
    for page in actual_pages:
        groups_by_key[(page.parent_path, page.title)].append(page)

    duplicate_groups: list[DuplicateCleanupGroup] = []
    for key, pages in sorted(groups_by_key.items()):
        expected_count = int(expected_counts.get(key) or 0)
        if expected_count <= 0 or len(pages) <= expected_count:
            continue

        preferred_ids = [
            page_id
            for page_id in state_page_groups.get(key, ())
            if any(page.page_id == page_id for page in pages)
        ]
        keep_ids: list[str] = []
        seen_keep_ids: set[str] = set()
        for page_id in preferred_ids:
            if page_id in seen_keep_ids or len(keep_ids) >= expected_count:
                continue
            keep_ids.append(page_id)
            seen_keep_ids.add(page_id)

        for page in sorted(pages, key=_child_page_sort_key, reverse=True):
            if len(keep_ids) >= expected_count:
                break
            if page.page_id in seen_keep_ids:
                continue
            keep_ids.append(page.page_id)
            seen_keep_ids.add(page.page_id)

        remove_ids = tuple(page.page_id for page in pages if page.page_id not in seen_keep_ids)
        if not remove_ids:
            continue
        duplicate_groups.append(
            DuplicateCleanupGroup(
                parent_path=key[0],
                title=key[1],
                expected_count=expected_count,
                actual_count=len(pages),
                redundant_count=len(remove_ids),
                keep_ids=tuple(keep_ids),
                remove_ids=remove_ids,
            )
        )

    return DuplicateCleanupPlan(
        source_dir=Path("."),
        root_page_id="",
        total_child_pages=len(actual_pages),
        groups=tuple(duplicate_groups),
        dry_run=True,
    )


def collect_existing_child_pages(
    *,
    root_page_id: str,
    notion_client: Any,
    progress: Callable[[str], None] | None = None,
) -> tuple[ExistingChildPage, ...]:
    pages: list[ExistingChildPage] = []
    stack: list[tuple[str, str]] = [(root_page_id, "")]

    while stack:
        parent_page_id, parent_path = stack.pop()
        for item in _iter_child_pages(notion_client, parent_page_id):
            if item.get("type") != "child_page" or item.get("in_trash"):
                continue
            title = str(item.get("child_page", {}).get("title") or "").strip()
            page_id = str(item.get("id") or "").strip()
            if not title or not page_id:
                continue
            current_path = f"{parent_path}/{title}" if parent_path else title
            pages.append(
                ExistingChildPage(
                    page_id=page_id,
                    title=title,
                    parent_path=parent_path,
                    path=current_path,
                    created_time=str(item.get("created_time") or ""),
                    last_edited_time=str(item.get("last_edited_time") or ""),
                )
            )
            if progress is not None:
                progress(f"scanned child page: {current_path}")
            stack.append((page_id, current_path))

    return tuple(pages)


def cleanup_duplicate_pages(
    *,
    source_dir: Path,
    root_page_id: str,
    notion_client: Any,
    state_path: Path,
    include_wiz_meta: bool = False,
    dry_run: bool = True,
    progress: Callable[[str], None] | None = None,
) -> DuplicateCleanupPlan:
    source_dir = source_dir.resolve()
    root_page_id = extract_page_id(root_page_id)
    state = _load_state(state_path)
    expected_counts = build_expected_child_page_counts(source_dir, include_wiz_meta=include_wiz_meta)
    state_page_groups = build_state_child_page_groups(state)
    actual_pages = collect_existing_child_pages(
        root_page_id=root_page_id,
        notion_client=notion_client,
        progress=progress,
    )
    base_plan = plan_duplicate_cleanup(
        expected_counts=expected_counts,
        state_page_groups=state_page_groups,
        actual_pages=actual_pages,
    )

    if not dry_run:
        for page_id in base_plan.page_ids_to_trash:
            if progress is not None:
                progress(f"moving duplicate page to trash: {page_id}")
            notion_client.trash_page(page_id)

    return DuplicateCleanupPlan(
        source_dir=source_dir,
        root_page_id=root_page_id,
        total_child_pages=base_plan.total_child_pages,
        groups=base_plan.groups,
        dry_run=dry_run,
    )


__all__ = [
    "DuplicateCleanupGroup",
    "DuplicateCleanupPlan",
    "ExistingChildPage",
    "build_expected_child_page_counts",
    "build_state_child_page_groups",
    "cleanup_duplicate_pages",
    "collect_existing_child_pages",
    "plan_duplicate_cleanup",
]
