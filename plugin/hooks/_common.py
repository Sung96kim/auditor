"""Shared stdlib helpers for the auditor plugin hooks (session_start / audit_edit / verify_stop).

Dependency-free by design — hooks run outside the auditor venv, so nothing here may import
`auditor` or any third-party package. Each hook lives in this directory, so `import _common`
resolves against `sys.path[0]` when the hook is run as a script."""

import contextlib
import json
import os
import shutil
import subprocess
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


#: the six values `auditr_observer._OFF` and `auditor.paths.OFF_VALUES` hold;
#: `tests/plugin/test_audit_edit.py::test_the_kill_switch_set_is_the_packages_own` pins all
#: three copies against `auditor.paths.OFF_VALUES`, which is the source of truth
_OFF = frozenset({"0", "f", "false", "n", "no", "off"})


def observe(event: str, payload: dict, timeout: float) -> bool:
    """Hand one hook payload to `auditr-observer hook <event>`, on its own stdin.

    The one place a plugin hook talks to the observer: the transport, the spool and the
    202-versus-400 rule all live in `auditr_observer.py`. False means no client is installed,
    which is the one case worth a word to the user.

    There is no `uvx` ladder behind the missing binary: `uvx --from "auditr[observer-claude]"`
    resolves a ~300MB SDK extra inside the few seconds a hook budget allows, so it is killed
    every time and never fills a cache.
    """
    if os.environ.get("AUDITOR_OBSERVER", "").strip().lower() in _OFF:
        return True
    found = shutil.which("auditr-observer")
    if found is None:
        return False
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            [found, "hook", event, "--client", "claude-code"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    return True
