# Wiz To Notion Importer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Python CLI that imports Markdown notes from `D:\notes\wiz-import-complete` into a target Notion page in one command.

**Architecture:** Scan the exported Markdown tree, strip Wiz/Obsidian frontmatter, create Notion child pages that mirror folders, create note pages through Notion's markdown page API, and optionally upload local assets through Notion File Uploads. Persist a state file and report under `_wiz` so interrupted imports can resume without duplicating pages.

**Tech Stack:** Python 3.10+, `requests`, `unittest`, Notion REST API version `2026-03-11`.

---

### Task 1: Markdown Preprocessing

**Files:**
- Create: `src/wiz_to_notion/markdown.py`
- Test: `tests/test_markdown.py`

**Steps:**
- Write tests for frontmatter stripping, title extraction, local asset detection, local asset placeholder rewriting, and external URL preservation.
- Implement a small parser that avoids a full Markdown AST but handles the exported Wiz patterns: Markdown images/links and HTML `src`/`href`.

### Task 2: Notion API Client

**Files:**
- Create: `src/wiz_to_notion/notion.py`
- Test: `tests/test_notion.py`

**Steps:**
- Write tests for page ID extraction, page create payloads, asset block shape, and chunking append requests at 100 blocks.
- Implement REST calls with retry handling for 429 and transient 5xx errors.

### Task 3: Import Orchestration

**Files:**
- Create: `src/wiz_to_notion/importer.py`
- Test: `tests/test_importer.py`

**Steps:**
- Write tests showing folder pages are created once, note pages are imported under their folder, and state-backed resume skips already imported notes.
- Implement discovery, folder creation, note import, optional asset upload, state updates, and report writing.

### Task 4: CLI And One-Click Script

**Files:**
- Create: `src/wiz_to_notion/cli.py`
- Create: `src/wiz_to_notion/__main__.py`
- Create: `scripts/import_wiz_to_notion.py`
- Create: `.env.example`
- Create: `README.md`
- Test: `tests/test_cli.py`

**Steps:**
- Write tests for `.env` loading and dry-run argument resolution.
- Implement `import` and `scan` commands plus a wrapper script that uses `.venv` when present.

### Task 5: Verification

**Commands:**
- `python -m unittest discover -s tests -v`
- `python .\scripts\import_wiz_to_notion.py --dry-run --limit 3`

