"""Shared stdlib helpers for the auditor plugin hooks (session_start / audit_edit / verify_stop).

Dependency-free by design — hooks run outside the auditor venv, so nothing here may import
`auditor` or any third-party package. Each hook lives in this directory, so `import _common`
resolves against `sys.path[0]` when the hook is run as a script."""

import json
import shutil
import sys

SEVERITY_RANK = {"suggestion": 0, "low": 1, "medium": 2, "high": 3, "blocking": 4}


def read_event() -> dict | None:
    """Parse the hook's stdin JSON. Returns the event object, or None if stdin is empty,
    malformed, or not a JSON object — all of which the caller treats as a silent no-op."""
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def auditr_available() -> bool:
    """Whether the `auditr` CLI is on PATH. Hooks stay silent when it isn't."""
    return shutil.which("auditr") is not None


def emit(output: dict) -> None:
    """Write a hook-output JSON object to stdout."""
    json.dump(output, sys.stdout)


def emit_context(event_name: str, context: str) -> None:
    """Emit `additionalContext` for a hook that feeds text back to the agent (SessionStart,
    PostToolUse)."""
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": context,
            }
        }
    )
