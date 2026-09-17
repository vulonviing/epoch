#!/usr/bin/env python3
"""Verify that a built EPOCH wheel contains every runtime prompt."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_ROOT = REPO_ROOT / "src/epoch_switch/agents"


def expected_prompt_paths() -> set[str]:
    return {
        path.relative_to(REPO_ROOT / "src").as_posix()
        for path in AGENTS_ROOT.rglob("*.md")
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_distribution.py DIST_WHEEL")
    wheel = Path(sys.argv[1])
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    missing = sorted(expected_prompt_paths() - names)
    if missing:
        raise RuntimeError(f"wheel is missing prompt/document files: {missing}")
    if any(name.startswith("epoch_switch/webapi/") for name in names):
        raise RuntimeError("CLI-only wheel unexpectedly contains epoch_switch.webapi")
    print(f"Distribution verification passed: {len(expected_prompt_paths())} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
