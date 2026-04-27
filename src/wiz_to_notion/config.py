from __future__ import annotations

import os
from pathlib import Path


DEFAULT_NOTION_VERSION = "2026-03-11"

SOURCE_ENV_VAR = "WIZ_TO_NOTION_SOURCE_DIR"
PARENT_ENV_VAR = "NOTION_PARENT_PAGE_ID"
TOKEN_ENV_VAR = "NOTION_TOKEN"
ALT_TOKEN_ENV_VAR = "NOTION_API_KEY"
VERSION_ENV_VAR = "NOTION_VERSION"


def default_source_dir() -> Path | None:
    value = os.environ.get(SOURCE_ENV_VAR)
    if value:
        return Path(value).expanduser()
    return None


def default_parent_page() -> str | None:
    value = os.environ.get(PARENT_ENV_VAR)
    return value or None


def default_notion_version() -> str:
    return os.environ.get(VERSION_ENV_VAR) or DEFAULT_NOTION_VERSION


def default_token() -> str | None:
    return os.environ.get(TOKEN_ENV_VAR) or os.environ.get(ALT_TOKEN_ENV_VAR) or None


__all__ = [
    "ALT_TOKEN_ENV_VAR",
    "DEFAULT_NOTION_VERSION",
    "PARENT_ENV_VAR",
    "SOURCE_ENV_VAR",
    "TOKEN_ENV_VAR",
    "VERSION_ENV_VAR",
    "default_notion_version",
    "default_parent_page",
    "default_source_dir",
    "default_token",
]
