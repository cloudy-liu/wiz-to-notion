from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_python(root: Path) -> Path:
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def build_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    src = str((root / "src").resolve())
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not existing else f"{src}{os.pathsep}{existing}"
    return env


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    root = repo_root()
    python = resolve_python(root)
    cli_args = argv if argv and argv[0] in {"scan", "import"} else ["import", *argv]
    completed = subprocess.run(
        [str(python), "-m", "wiz_to_notion.cli", *cli_args],
        cwd=root,
        env=build_env(root),
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

