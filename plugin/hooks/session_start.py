#!/usr/bin/env python3
"""SessionStart hook: tell the agent whether auditor is available + configured for this repo."""

import json
import shutil
import sys
from pathlib import Path


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
    configured = (cwd / ".auditor" / "config.toml").exists()
    state = "configured" if configured else "not yet configured (run /auditor:setup)"
    _emit(
        "auditor (deterministic code auditor) is available. "
        f"This repo is {state}. "
        "Judge findings with /auditor:judge-findings, review a diff with /auditor:audit-changes."
    )


if __name__ == "__main__":
    main()
