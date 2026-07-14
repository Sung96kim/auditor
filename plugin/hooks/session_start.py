#!/usr/bin/env python3
"""SessionStart hook: tell the agent whether auditor is available + configured for this repo."""

import tomllib
from pathlib import Path

from _common import auditr_available, emit_context, read_event


def is_configured(cwd: Path) -> bool:
    """True if `.auditor/config.toml` exists or `pyproject.toml` has a `[tool.auditor]` table.
    Mirrors `auditor.config.is_configured` in stdlib only (this hook can't import auditor)."""
    if (cwd / ".auditor" / "config.toml").exists():
        return True
    try:
        data = tomllib.loads((cwd / "pyproject.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return "auditor" in data.get("tool", {})


def main() -> None:
    event = read_event()
    if event is None or not auditr_available():
        return
    cwd = Path(event.get("cwd") or ".")
    state = (
        "configured"
        if is_configured(cwd)
        else "not yet configured (run /auditor:setup)"
    )
    emit_context(
        "SessionStart",
        "auditor (deterministic code auditor) is available. "
        f"This repo is {state}. "
        "Judge findings with /auditor:judge-findings, review a diff with /auditor:audit-changes.",
    )


if __name__ == "__main__":
    main()
