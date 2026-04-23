from __future__ import annotations

from dataclasses import dataclass
import mimetypes
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable


NOTION_BASE_URL = "https://api.notion.com/v1"
PAGE_ID_HEX = re.compile(r"[0-9a-fA-F]{32}")
PAGE_ID_DASHED = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp"}
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".wav"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv"}


@dataclass(frozen=True)
class NotionApiError(RuntimeError):
    status_code: int
    code: str
    message: str

    def __str__(self) -> str:
        return f"Notion API error {self.status_code} {self.code}: {self.message}"


def extract_page_id(value: str) -> str:
    text = value.strip()
    dashed = PAGE_ID_DASHED.findall(text)
    if dashed:
        compact = dashed[-1].replace("-", "")
    else:
        compact_candidates = PAGE_ID_HEX.findall(text)
        if not compact_candidates:
            raise ValueError(f"Not a valid Notion page id or URL: {value}")
        compact = compact_candidates[-1]
    compact = compact.lower()
    return f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:32]}"


def rich_text(content: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": content[:2000]}}] if content else []


def build_create_page_payload(*, parent_page_id: str, title: str, markdown: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "properties": {
            "title": {
                "title": rich_text(title or "Untitled"),
            }
        },
    }
    if markdown is not None and markdown.strip():
        payload["markdown"] = markdown
    return payload


def _block_type_for_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return "file"


def build_file_block(upload_id: str, path: Path, *, caption: str = "") -> dict[str, Any]:
    block_type = _block_type_for_file(path)
    block: dict[str, Any] = {
        "object": "block",
        "type": block_type,
        block_type: {
            "type": "file_upload",
            "file_upload": {"id": upload_id},
        },
    }
    if caption:
        block[block_type]["caption"] = rich_text(caption)
    return block


def build_heading_block(text: str, *, level: int = 2) -> dict[str, Any]:
    block_type = f"heading_{max(1, min(level, 3))}"
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": rich_text(text)},
    }


def build_paragraph_block(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": rich_text(text)},
    }


def build_plain_text_blocks(text: str, *, max_chars: int = 1900) -> list[dict[str, Any]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    blocks: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(normalized):
        end = min(len(normalized), cursor + max_chars)
        if end < len(normalized):
            paragraph_break = normalized.rfind("\n\n", cursor, end)
            line_break = normalized.rfind("\n", cursor, end)
            space_break = normalized.rfind(" ", cursor, end)
            split_at = max(paragraph_break, line_break, space_break)
            if split_at > cursor + max_chars // 2:
                end = split_at
        chunk = normalized[cursor:end].strip()
        if chunk:
            blocks.append(build_paragraph_block(chunk))
        cursor = end
        while cursor < len(normalized) and normalized[cursor].isspace():
            cursor += 1
    return blocks


def chunked(items: Iterable[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    chunk: list[dict[str, Any]] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


class NotionClient:
    def __init__(
        self,
        token: str,
        *,
        notion_version: str,
        base_url: str = NOTION_BASE_URL,
        session: Any = None,
        request_delay: float = 0.35,
        max_retries: int = 5,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("The requests package is required. Run: python -m pip install -r requirements.txt") from exc

        self.token = token
        self.notion_version = notion_version
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.request_delay = max(0.0, request_delay)
        self.max_retries = max_retries
        self.sleep_fn = sleep_fn
        self._last_request_at = 0.0

    def _headers(self, *, json_content: bool = True) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.notion_version,
        }
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _throttle(self) -> None:
        if self.request_delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.request_delay - elapsed
        if remaining > 0:
            self.sleep_fn(remaining)

    def request(self, method: str, path: str, *, json: dict[str, Any] | None = None, files: Any = None) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        json_content = files is None
        last_error: NotionApiError | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            response = self.session.request(
                method,
                url,
                headers=self._headers(json_content=json_content),
                json=json,
                files=files,
            )
            self._last_request_at = time.monotonic()
            if 200 <= response.status_code < 300:
                if not response.content:
                    return {}
                return response.json()

            payload = _safe_json(response)
            code = str(payload.get("code") or response.reason or "error")
            message = str(payload.get("message") or response.text or "")
            last_error = NotionApiError(response.status_code, code, message)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < self.max_retries:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait_seconds = float(retry_after)
                        except ValueError:
                            wait_seconds = 1.0
                    else:
                        wait_seconds = min(30.0, 2.0**attempt)
                    self.sleep_fn(wait_seconds)
                    continue
            raise last_error
        if last_error is not None:
            raise last_error
        raise RuntimeError("Notion request failed without a response")

    def create_page(self, *, parent_page_id: str, title: str, markdown: str | None = None) -> dict[str, Any]:
        return self.request(
            "POST",
            "/pages",
            json=build_create_page_payload(parent_page_id=parent_page_id, title=title, markdown=markdown),
        )

    def create_file_upload(self, path: Path) -> dict[str, Any]:
        content_type, _ = mimetypes.guess_type(path.name)
        payload: dict[str, Any] = {"filename": path.name}
        if content_type:
            payload["content_type"] = content_type
        return self.request("POST", "/file_uploads", json=payload)

    def send_file_upload(self, upload_id: str, path: Path) -> dict[str, Any]:
        content_type, _ = mimetypes.guess_type(path.name)
        with path.open("rb") as file_obj:
            files = {"file": (path.name, file_obj, content_type or "application/octet-stream")}
            return self.request("POST", f"/file_uploads/{upload_id}/send", files=files)

    def upload_file(self, path: Path) -> str:
        upload = self.create_file_upload(path)
        upload_id = str(upload["id"])
        self.send_file_upload(upload_id, path)
        return upload_id

    def append_blocks(self, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        last_response: dict[str, Any] = {}
        for block_chunk in chunked(children, 100):
            last_response = self.request(
                "PATCH",
                f"/blocks/{block_id}/children",
                json={"children": block_chunk},
            )
        return last_response


def _safe_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


__all__ = [
    "NotionApiError",
    "NotionClient",
    "build_create_page_payload",
    "build_file_block",
    "build_heading_block",
    "build_paragraph_block",
    "build_plain_text_blocks",
    "chunked",
    "extract_page_id",
]
