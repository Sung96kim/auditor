#!/usr/bin/env python3
"""Stop hook (opt-in): block finishing if the changeset still trips the auditor gate.

Disabled unless AUDITOR_VERIFY_HOOK=1. AUDITOR_VERIFY_SEVERITY sets the gate floor (default high)."""

import json
import os
import subprocess

from _common import auditr_available, emit, read_event


def emitted_scan_report(stdout: str) -> bool:
    """Whether `stdout` is a completed `auditr scan` JSON report (a dict with `files`/`totals`).

    Exit code alone can't disambiguate a real gate trip from a tool error — auditr exits 1 for
    both, and `scan -f json` emits no `gate` key. But a completed scan always writes its report
    before the gate trips, whereas a tool/config error (not a git repo, bad args) exits without
    one — so the report's presence is the reliable "real trip" signal."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(data, dict) and ("files" in data or "totals" in data)


def main() -> None:
    if os.environ.get("AUDITOR_VERIFY_HOOK") != "1" or not auditr_available():
        return
    event = read_event()
    if event is None:
        return
    cwd = event.get("cwd") or "."
    floor = os.environ.get("AUDITOR_VERIFY_SEVERITY", "high")
    # --since HEAD gates only the uncommitted delta; changes folded into a mid-session commit
    # aren't re-gated by a later run of this hook.
    proc = subprocess.run(
        ["auditr", "scan", cwd, "--since", "HEAD", "-f", "json", "--fail-on", floor],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return  # clean → allow finishing
    if emitted_scan_report(proc.stdout):
        emit(
            {
                "decision": "block",
                "reason": (
                    f"auditor gate ({floor}+) still trips on this change. "
                    "Run /auditor:judge-findings and resolve the blocking/high findings before finishing."
                ),
            }
        )
    else:
        # Tool/config error, not a findings verdict — surface a note but DON'T block; a hiccup
        # must not wedge the agent from finishing.
        emit(
            {
                "systemMessage": (
                    f"auditor verify-stop: couldn't evaluate the changeset (exit {proc.returncode}) "
                    "— gate not enforced this turn. Check that `auditr` is set up and this is a "
                    "git repository."
                )
            }
        )


if __name__ == "__main__":
    main()
