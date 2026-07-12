#!/usr/bin/env python3
"""PostToolUse(Edit|Write) hook: audit the changed file and feed findings back to the agent.

Default: sync, single-file `auditr report`, injects findings in-turn. AUDITOR_AUTOHOOK_ASYNC=1
detaches an incremental repo scan (refreshes the status line) and returns immediately."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".bash"}
RANK = {"suggestion": 0, "low": 1, "medium": 2, "high": 3, "blocking": 4}


def _emit(context: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )


def _detach_incremental_scan(root: Path) -> None:
    subprocess.Popen(
        ["auditr", "scan", str(root), "-i", "-f", "json"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _report(file_path: Path) -> dict | None:
    proc = subprocess.run(
        ["auditr", "report", str(file_path), "-f", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def _summarize(report: dict, floor: int) -> str | None:
    detail: list[str] = []
    rolled: dict[str, int] = {}
    for f in report.get("files", []):
        for finding in f.get("findings", []):
            sev = finding.get("severity", "suggestion")
            is_auto = finding.get("verdict_kind") == "auto"
            shown = sev == "blocking" or (is_auto and RANK.get(sev, 0) >= floor)
            if shown:
                detail.append(
                    f"  [{sev}] {finding.get('rule_id')} L{finding.get('line')}: {finding.get('message')}"
                )
            else:
                rolled[sev] = rolled.get(sev, 0) + 1
    if not detail and not rolled:
        return None
    lines = []
    if detail:
        lines.append("auditor flagged the file you just changed:")
        lines.extend(detail)
    if rolled:
        parts = [
            f"{n} {sev}"
            for sev, n in sorted(rolled.items(), key=lambda kv: -RANK.get(kv[0], 0))
        ]
        lines.append("+" + ", ".join(parts) + " lower — run /auditor:judge-findings")
    return "\n".join(lines)


def main() -> None:
    if os.environ.get("AUDITOR_AUTOHOOK") == "0":
        return
    if shutil.which("auditr") is None:
        return
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(payload, dict):
        return  # valid JSON but not an object → nothing to do
    file_path = Path((payload.get("tool_input") or {}).get("file_path", ""))
    if file_path.suffix not in SUFFIXES:
        return
    if os.environ.get("AUDITOR_AUTOHOOK_ASYNC") == "1":
        _detach_incremental_scan(Path(payload.get("cwd") or "."))
        return
    report = _report(file_path)
    if report is None:
        return
    floor = RANK.get(os.environ.get("AUDITOR_AUTOHOOK_SEVERITY", "high"), RANK["high"])
    summary = _summarize(report, floor)
    if summary:
        _emit(summary)


if __name__ == "__main__":
    main()
