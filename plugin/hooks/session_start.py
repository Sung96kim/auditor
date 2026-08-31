#!/usr/bin/env python3
"""SessionStart hook: tell the agent whether auditor is available + configured for this repo."""

import sys
import tomllib
from pathlib import Path

from _common import auditr_available, emit_context, observe, read_event


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


#: spec 13.1's budget, and what stops a cold `ensure` blocking the session: the launch may spend
#: longer than this inside, and the next Stop's heartbeat re-attaches when it does (P30)
OBSERVE_TIMEOUT = 3.0


def main() -> None:
    event = read_event()
    if event is None:
        return
    if not observe("session-start", event, OBSERVE_TIMEOUT):
        print(
            "auditor: auditr-observer is not installed, so the graph observer is off",
            file=sys.stderr,
        )
    if not auditr_available():
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
        "Judge findings with /auditor:judge-findings, review a diff with /auditor:audit-changes. "
        "auditor also keeps a semantic code graph for this repo: explore it with "
        "/auditor:explore-graph, and see what the observer refined with /auditor:graph-observer.",
    )


if __name__ == "__main__":
    main()
