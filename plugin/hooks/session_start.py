#!/usr/bin/env python3
"""SessionStart hook: tell the agent whether auditor is available + configured for this repo."""

import json
import shutil
import sys
import tomllib
from pathlib import Path


def _is_configured(cwd: Path) -> bool:
    """True if ``.auditor/config.toml`` exists or ``pyproject.toml`` has a
    ``[tool.auditor]`` table. Mirrors ``auditor.config.is_configured`` in stdlib
    only (this hook can't import the ``auditor`` package)."""
    if (cwd / ".auditor" / "config.toml").exists():
        return True
    pp = cwd / "pyproject.toml"
    if not pp.exists():
        return False
    try:
        data = tomllib.loads(pp.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return "auditor" in data.get("tool", {})


def _emit(context: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(payload, dict):
        return  # valid JSON but not an object → nothing to do
    if shutil.which("auditr") is None:
        return  # tool not installed → say nothing
    cwd = Path(payload.get("cwd") or ".")
    configured = _is_configured(cwd)
    state = "configured" if configured else "not yet configured (run /auditor:setup)"
    _emit(
        "auditor (deterministic code auditor) is available. "
        f"This repo is {state}. "
        "Judge findings with /auditor:judge-findings, review a diff with /auditor:audit-changes."
    )


if __name__ == "__main__":
    main()
