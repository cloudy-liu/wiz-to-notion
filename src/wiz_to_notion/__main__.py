from __future__ import annotations

try:
    from wiz_to_notion.cli import main
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from wiz_to_notion.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
