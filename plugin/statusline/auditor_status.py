#!/usr/bin/env python3
"""Status line: compact auditor posture from $AUDITOR_HOME/repos/<key>/status.json.

Stdlib only, and whatever `python3` Claude Code resolves runs it, so it targets 3.9+ and assumes
no `tomllib`. That is why it re-implements the pieces it needs — `auditor.discovery.find_root`,
`auditor.paths.repo_identity`, `auditor.paths.repo_dir_key` and `auditor.paths.auditor_home` —
instead of importing the package; `tests/plugin/test_statusline.py` pins each against its twin.
`git rev-parse` is the only subprocess (twice outside git, for the pre-2.31 fallback); the
database is never opened.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

RED, ORANGE, GREEN, DIM, RESET = (
    "\033[31m",
    "\033[33m",
    "\033[32m",
    "\033[2m",
    "\033[0m",
)
STALE_SECONDS = 900
NOT_SET_UP = f"{DIM}○ auditor  not set up{RESET}"
_ROOT_MARKERS = (".git", "pyproject.toml", ".auditor")


def _num(value: object) -> float:
    """A number from untrusted JSON, or 0. Bools are excluded: `True` is an `int` and would read
    as a count of 1."""
    return (
        value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
    )


def _find_root(start: Path) -> Path:
    """The repo root, exactly as `discovery.find_root` resolves it."""
    start = start if start.is_dir() else start.parent
    for parent in [start, *start.parents]:
        if any((parent / marker).exists() for marker in _ROOT_MARKERS):
            return parent
    return start


def _git_output(root: Path, *args: str) -> str | None:
    """Stripped stdout of a git subcommand, or None when git is missing or the command fails —
    the same sentinel `discovery.git_output` uses, so the two cannot drift apart."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _repo_identity(root: Path) -> str:
    """The identity `paths.repo_identity` computes: resolved git common dir, else resolved root.

    Both git branches resolve, exactly as the package does, or the two would disagree on a
    symlinked checkout and the status line would read an empty directory.
    """
    absolute = _git_output(
        root, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    if absolute is not None:
        return str(Path(absolute).resolve())
    relative = _git_output(root, "rev-parse", "--git-common-dir")  # git < 2.31
    if relative is not None:
        return str((root / relative).resolve())
    return str(root.resolve())


def _repo_dir_key(root: Path) -> str:
    return hashlib.sha1(
        _repo_identity(root).encode(), usedforsecurity=False
    ).hexdigest()


def _home() -> Path:
    raw = os.environ.get("AUDITOR_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".auditor"


def _status_path(cwd: Path) -> Path:
    return _home() / "repos" / _repo_dir_key(_find_root(cwd)) / "status.json"


def _render(cwd: Path) -> str:
    try:
        data = json.loads(_status_path(cwd).read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        # A missing, torn or unreadable cache degrades to the same sentinel — it must not make
        # the whole segment vanish.
        return NOT_SET_UP
    scan = data.get("scan") if isinstance(data, dict) else None
    if not isinstance(scan, dict):
        return NOT_SET_UP
    sev = scan.get("severity", {})
    if not isinstance(sev, dict):
        sev = {}
    blocking, high = _num(sev.get("blocking")), _num(sev.get("high"))
    lower = _num(sev.get("medium")) + _num(sev.get("low")) + _num(sev.get("suggestion"))
    if not scan.get("configured", True) and not (blocking or high or lower):
        return NOT_SET_UP
    if not (blocking or high or lower):
        return f"{GREEN}●{RESET} auditor  clean"
    dot = RED if blocking else (ORANGE if high else DIM)
    parts = []
    if blocking:
        parts.append(f"{RED}{blocking} blocking{RESET}")
    if high:
        parts.append(f"{ORANGE}{high} high{RESET}")
    if lower:
        parts.append(f"{DIM}+{lower} lower{RESET}")
    line = f"{dot}●{RESET} auditor  " + "  ".join(parts)
    if time.time() - _num(scan.get("written_at")) > STALE_SECONDS:
        line += f"  {DIM}⟳{RESET}"
    return line


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    cwd_raw = payload.get("cwd")
    cwd = Path(cwd_raw) if isinstance(cwd_raw, str) and cwd_raw else Path(".")
    sys.stdout.write(_render(cwd))


if __name__ == "__main__":
    main()
