#!/usr/bin/env python3
"""Status line: compact auditor posture from .auditor/.status.json. No subprocess, no DB."""

import json
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


def _num(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _render(cwd: Path) -> str | None:
    cache = cwd / ".auditor" / ".status.json"
    if not cache.exists():
        return f"{DIM}○ auditor  not set up{RESET}"
    try:
        data = json.loads(cache.read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    sev = data.get("severity", {})
    if not isinstance(sev, dict):
        sev = {}
    blocking, high = _num(sev.get("blocking")), _num(sev.get("high"))
    lower = _num(sev.get("medium")) + _num(sev.get("low")) + _num(sev.get("suggestion"))
    if not data.get("configured", True) and not (blocking or high or lower):
        return f"{DIM}○ auditor  not set up{RESET}"
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
    written_at = data.get("written_at")
    written_at = (
        written_at
        if isinstance(written_at, (int, float)) and not isinstance(written_at, bool)
        else 0
    )
    if time.time() - written_at > STALE_SECONDS:
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
    line = _render(cwd)
    if line:
        sys.stdout.write(line)


if __name__ == "__main__":
    main()
