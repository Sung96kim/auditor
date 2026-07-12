#!/usr/bin/env python3
"""Stop hook (opt-in): block finishing if the changeset still trips the auditor gate.

Disabled unless AUDITOR_VERIFY_HOOK=1. AUDITOR_VERIFY_SEVERITY sets the gate floor (default high)."""

import json
import os
import shutil
import subprocess
import sys


def main() -> None:
    if os.environ.get("AUDITOR_VERIFY_HOOK") != "1":
        return
    if shutil.which("auditr") is None:
        return
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(payload, dict):
        return  # valid JSON but not an object → nothing to do
    cwd = payload.get("cwd") or "."
    floor = os.environ.get("AUDITOR_VERIFY_SEVERITY", "high")
    # --since HEAD only gates the uncommitted delta — changes folded into a mid-session
    # commit drop out of scope and won't be re-gated by a later run of this hook.
    proc = subprocess.run(
        ["auditr", "scan", cwd, "--since", "HEAD", "-f", "json", "--fail-on", floor],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return  # clean → allow finishing
    # Exit code alone can't disambiguate: `auditr` uses exit 1 for BOTH a real gate trip AND
    # `fail()` business errors (not a git repo, bad severity), and `scan -f json` emits no
    # top-level "gate" key (only the MCP tool does). The reliable signal is stdout: a completed
    # scan emits its JSON report (with `files`/`totals`) even when the gate then trips, whereas a
    # tool/config error exits without a valid scan payload. So: valid payload → real trip (block);
    # no payload → auditr couldn't evaluate (surface a note, but DON'T block — a tool hiccup must
    # not wedge the agent from finishing).
    try:
        parsed = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    is_scan_payload = isinstance(parsed, dict) and (
        "files" in parsed or "totals" in parsed
    )
    if is_scan_payload:
        json.dump(
            {
                "decision": "block",
                "reason": (
                    f"auditor gate ({floor}+) still trips on this change. "
                    "Run /auditor:judge-findings and resolve the blocking/high findings before finishing."
                ),
            },
            sys.stdout,
        )
    else:
        json.dump(
            {
                "systemMessage": (
                    f"auditor verify-stop hook: couldn't evaluate the changeset "
                    f"(exit {proc.returncode}) — gate not enforced this turn. Check that `auditr` "
                    "is set up and this is a git repository."
                )
            },
            sys.stdout,
        )


if __name__ == "__main__":
    main()
