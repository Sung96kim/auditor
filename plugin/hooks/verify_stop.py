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
    proc = subprocess.run(
        ["auditr", "scan", cwd, "--since", "HEAD", "-f", "json", "--fail-on", floor],
        capture_output=True,
        text=True,
    )
    try:
        parsed = json.loads(proc.stdout)
        gate = parsed.get("gate") if isinstance(parsed, dict) else None
        tripped = (
            bool(gate.get("tripped"))
            if isinstance(gate, dict)
            else (proc.returncode != 0)
        )
    except (json.JSONDecodeError, ValueError):
        tripped = proc.returncode != 0
    if tripped:
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


if __name__ == "__main__":
    main()
