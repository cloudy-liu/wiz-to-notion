from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import sys
from pathlib import Path
from typing import TextIO

from .config import (
    ALT_TOKEN_ENV_VAR,
    PARENT_ENV_VAR,
    SOURCE_ENV_VAR,
    TOKEN_ENV_VAR,
    VERSION_ENV_VAR,
    default_notion_version,
    default_parent_page,
    default_source_dir,
    default_token,
)
from .cleanup import cleanup_duplicate_pages
from .importer import import_markdown_tree, scan_markdown_tree
from .notion import NotionApiError, NotionClient, extract_page_id


def _split_dotenv_assignment(line: str) -> tuple[str, str] | None:
    if "=" in line:
        key, value = line.split("=", 1)
        return key.strip(), value.strip()
    if ":" in line:
        key, value = line.split(":", 1)
        return key.strip(), value.strip()
    return None


def _default_dotenv_path() -> Path:
    cwd_path = Path.cwd() / ".env"
    if cwd_path.exists():
        return cwd_path

    if getattr(sys, "frozen", False):
        executable_path = Path(sys.executable).resolve().parent / ".env"
        if executable_path.exists():
            return executable_path

    return cwd_path


def load_dotenv(path: Path | None = None) -> None:
    path = path or _default_dotenv_path()
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        assignment = _split_dotenv_assignment(line)
        if assignment is None:
            continue
        key, value = assignment
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wiz-to-notion")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan Markdown notes and report import size.")
    scan_parser.add_argument("--source", type=Path, default=None)
    scan_parser.add_argument("--limit", type=int, default=None)
    scan_parser.add_argument("--include-wiz-meta", action="store_true")

    import_parser = subparsers.add_parser("import", help="Import Markdown notes into Notion.")
    import_parser.add_argument("--source", type=Path, default=None)
    import_parser.add_argument("--parent", default=None, help="Target Notion page id or URL.")
    import_parser.add_argument("--token", default=None, help=f"Notion integration token. Defaults to {TOKEN_ENV_VAR}.")
    import_parser.add_argument("--notion-version", default=None)
    import_parser.add_argument("--limit", type=int, default=None)
    import_parser.add_argument("--dry-run", action="store_true")
    import_parser.add_argument("--no-resume", action="store_true")
    import_parser.add_argument("--skip-assets", action="store_true")
    import_parser.add_argument("--include-wiz-meta", action="store_true")
    import_parser.add_argument("--state", type=Path, default=None)
    import_parser.add_argument("--report", type=Path, default=None)
    import_parser.add_argument("--max-upload-mb", type=float, default=20.0)
    import_parser.add_argument("--request-delay", type=float, default=0.35)
    import_parser.add_argument("--stop-on-error", action="store_true")

    cleanup_parser = subparsers.add_parser("cleanup-duplicates", help="Find and optionally trash redundant duplicate Notion pages.")
    cleanup_parser.add_argument("--source", type=Path, default=None)
    cleanup_parser.add_argument("--parent", default=None, help="Target Notion page id or URL.")
    cleanup_parser.add_argument("--token", default=None, help=f"Notion integration token. Defaults to {TOKEN_ENV_VAR}.")
    cleanup_parser.add_argument("--notion-version", default=None)
    cleanup_parser.add_argument("--state", type=Path, default=None)
    cleanup_parser.add_argument("--include-wiz-meta", action="store_true")
    cleanup_parser.add_argument("--request-delay", type=float, default=0.35)
    cleanup_parser.add_argument("--apply", action="store_true", help="Actually move redundant pages to trash.")
    return parser


def _write_json(stdout: TextIO, payload: dict) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    stdout.write("\n")


def _progress(stderr: TextIO, message: str) -> None:
    stderr.write(f"[import] {message}\n")
    stderr.flush()


def _write_import_summary(stderr: TextIO, result: object) -> None:
    notes = getattr(result, "notes", ())
    summary = getattr(result, "summary")
    counts = Counter(getattr(note, "status", "") for note in notes)

    if summary.dry_run:
        stderr.write("Dry run complete.\n")
        stderr.write(f"Scanned notes: {summary.total_notes}\n")
        stderr.write(f"Would import: {counts.get('would_import', 0)}\n")
        stderr.write(f"Skipped unchanged: {summary.skipped_notes}\n")
        stderr.write(f"Failed: {summary.failed_notes}\n")
        stderr.write(f"Unresolved links: {summary.unresolved_links}\n")
        stderr.write("State/report files were not written because this was a dry run.\n")
    else:
        imported = sum(count for status, count in counts.items() if status.startswith("imported"))
        updated = sum(count for status, count in counts.items() if status.startswith("updated"))
        stderr.write("Import complete.\n")
        stderr.write(f"Scanned notes: {summary.total_notes}\n")
        stderr.write(f"Imported new pages: {imported}\n")
        stderr.write(f"Updated existing pages: {updated}\n")
        stderr.write(f"Skipped unchanged: {summary.skipped_notes}\n")
        stderr.write(f"Failed: {summary.failed_notes}\n")
        stderr.write(f"Uploaded assets: {summary.uploaded_assets}\n")
        stderr.write(f"Skipped assets: {summary.skipped_assets}\n")
        stderr.write(f"Unresolved links: {summary.unresolved_links}\n")
        stderr.write(f"State file: {result.state_path}\n")
        stderr.write(f"Report file: {result.report_path}\n")

    failed_notes = [note for note in notes if getattr(note, "status", "") == "failed"]
    if failed_notes:
        stderr.write("Failed notes:\n")
        for note in failed_notes[:5]:
            error = getattr(note, "error", "") or "unknown error"
            stderr.write(f"- {note.relative_path}: {error}\n")
        remaining = len(failed_notes) - 5
        if remaining > 0:
            stderr.write(f"- ... and {remaining} more\n")
    stderr.flush()


def _write_cleanup_summary(stderr: TextIO, payload: dict[str, object], *, applied: bool) -> None:
    groups = payload.get("duplicate_groups", [])
    page_ids = payload.get("page_ids_to_trash", [])
    stderr.write("Duplicate cleanup complete.\n" if applied else "Duplicate cleanup dry run complete.\n")
    stderr.write(f"Scanned child pages: {payload.get('total_child_pages', 0)}\n")
    stderr.write(f"Duplicate groups: {len(groups) if isinstance(groups, list) else 0}\n")
    stderr.write(f"Pages to trash: {len(page_ids) if isinstance(page_ids, list) else 0}\n")
    if isinstance(groups, list) and groups:
        stderr.write("Duplicate groups:\n")
        for group in groups[:10]:
            stderr.write(
                f"- {(group.get('parent_path') or '<root>')}/{group.get('title')}: "
                f"actual={group.get('actual_count')} expected={group.get('expected_count')} "
                f"remove={len(group.get('remove_ids') or [])}\n"
            )
        remaining = len(groups) - 10
        if remaining > 0:
            stderr.write(f"- ... and {remaining} more\n")
    stderr.flush()


def _write_notion_error(stderr: TextIO, error: NotionApiError, *, parent_page_id: str) -> None:
    stderr.write(f"Notion API error {error.status_code} {error.code}: {error.message}\n")
    if error.status_code == 404 and error.code == "object_not_found":
        stderr.write("\n")
        stderr.write("The token was accepted, but the target page is not visible to this integration.\n")
        stderr.write(f"Target page id: {parent_page_id}\n")
        stderr.write("Open the target Notion page, click ... -> Add connections, then add this integration.\n")
        stderr.write("If it is already connected, confirm the page belongs to the same Notion workspace as the integration.\n")
    stderr.flush()


def _resolved_source(source_arg: Path | None) -> Path:
    if source_arg is not None:
        return source_arg.expanduser()
    source_dir = default_source_dir()
    if source_dir is None:
        raise ValueError(f"missing source directory: pass --source or set {SOURCE_ENV_VAR} in .env.")
    return source_dir


def _resolved_parent_page(parent_arg: str | None) -> str:
    if parent_arg:
        return parent_arg
    parent_page = default_parent_page()
    if parent_page:
        return parent_page
    raise ValueError(f"missing Notion parent page: pass --parent or set {PARENT_ENV_VAR} in .env.")


def main(argv: list[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "scan":
        try:
            source_dir = _resolved_source(args.source)
        except ValueError as exc:
            parser.error(str(exc))
        payload = scan_markdown_tree(
            source_dir=source_dir,
            limit=args.limit,
            include_wiz_meta=args.include_wiz_meta,
        )
        _write_json(stdout, payload)
        return 0

    if args.command == "import":
        try:
            source_dir = _resolved_source(args.source)
            parent_value = _resolved_parent_page(args.parent)
        except ValueError as exc:
            parser.error(str(exc))
        parent_page_id = extract_page_id(parent_value)
        token = args.token or default_token()
        notion_version = args.notion_version or default_notion_version()

        if not args.dry_run and not token:
            parser.error(
                "missing Notion token: pass --token or set "
                f"{TOKEN_ENV_VAR}=ntn_... in .env. {ALT_TOKEN_ENV_VAR} is also accepted."
            )

        notion_client = None
        if not args.dry_run:
            notion_client = NotionClient(
                token=token or "",
                notion_version=notion_version,
                request_delay=args.request_delay,
            )

        try:
            result = import_markdown_tree(
                source_dir=source_dir,
                parent_page_id=parent_page_id,
                notion_client=notion_client,
                state_path=args.state,
                report_path=args.report,
                limit=args.limit,
                dry_run=args.dry_run,
                resume=not args.no_resume,
                upload_assets=not args.skip_assets,
                include_wiz_meta=args.include_wiz_meta,
                max_upload_bytes=int(args.max_upload_mb * 1024 * 1024),
                progress=lambda message: _progress(stderr, message),
                stop_on_error=args.stop_on_error,
            )
        except NotionApiError as exc:
            _write_notion_error(stderr, exc, parent_page_id=parent_page_id)
            return 1
        payload = result.to_dict()
        payload["notion_version"] = notion_version
        payload["token_env"] = TOKEN_ENV_VAR
        payload["source_env"] = SOURCE_ENV_VAR
        payload["parent_env"] = PARENT_ENV_VAR
        payload["version_env"] = VERSION_ENV_VAR
        _write_json(stdout, payload)
        _write_import_summary(stderr, result)
        return 0

    if args.command == "cleanup-duplicates":
        try:
            source_dir = _resolved_source(args.source)
            parent_value = _resolved_parent_page(args.parent)
        except ValueError as exc:
            parser.error(str(exc))
        parent_page_id = extract_page_id(parent_value)
        token = args.token or default_token()
        notion_version = args.notion_version or default_notion_version()

        if not token:
            parser.error(
                "missing Notion token: pass --token or set "
                f"{TOKEN_ENV_VAR}=ntn_... in .env. {ALT_TOKEN_ENV_VAR} is also accepted."
            )

        notion_client = NotionClient(
            token=token,
            notion_version=notion_version,
            request_delay=args.request_delay,
        )
        state_path = args.state or (source_dir / "_wiz" / "notion_import_state.json")
        try:
            plan = cleanup_duplicate_pages(
                source_dir=source_dir,
                root_page_id=parent_page_id,
                notion_client=notion_client,
                state_path=state_path,
                include_wiz_meta=args.include_wiz_meta,
                dry_run=not args.apply,
                progress=lambda message: _progress(stderr, message),
            )
        except NotionApiError as exc:
            _write_notion_error(stderr, exc, parent_page_id=parent_page_id)
            return 1
        payload = plan.to_dict()
        payload["notion_version"] = notion_version
        payload["state_path"] = str(state_path)
        _write_json(stdout, payload)
        _write_cleanup_summary(stderr, payload, applied=args.apply)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
