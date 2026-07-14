#!/usr/bin/env python3
"""PostToolUse(Edit|Write) hook: audit the changed file and feed findings back to the agent.

Default: sync — a single-file `auditr report`, findings injected in-turn. `AUDITOR_AUTOHOOK_ASYNC=1`
detaches an incremental repo scan (refreshes the status line) and returns immediately.
`AUDITOR_AUTOHOOK=0` disables the hook; `AUDITOR_AUTOHOOK_SEVERITY` sets the inline floor (default high)."""

import json
import os
import subprocess
from pathlib import Path

from _common import SEVERITY_RANK, auditr_available, emit_context, read_event

SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".bash"}


def changed_file(event: dict) -> Path | None:
    """The auditable file the Edit/Write touched, or None if the payload lacks one or its
    suffix isn't a language auditor scans."""
    tool_input = event.get("tool_input")
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str) or not path:
        return None
    file = Path(path)
    return file if file.suffix in SUFFIXES else None


def detach_incremental_scan(root: Path) -> None:
    """Fire-and-forget an incremental repo scan to refresh the status line, without blocking."""
    subprocess.Popen(
        ["auditr", "scan", str(root), "-i", "-f", "json"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def report(file: Path) -> dict | None:
    """`auditr report <file>` as a dict, or None on any failure / malformed output."""
    proc = subprocess.run(
        ["auditr", "report", str(file), "-f", "json"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def summarize(result: dict, floor: int) -> str | None:
    """Agent-facing text for one file's findings: those at/above `floor` (or any `blocking`,
    whatever the verdict) shown in detail, the rest rolled into a one-line count. None if empty."""
    shown: list[str] = []
    rolled: dict[str, int] = {}
    for file in result.get("files") or []:
        findings = file.get("findings") if isinstance(file, dict) else None
        for finding in findings or []:
            if not isinstance(finding, dict):
                continue
            sev = finding.get("severity", "suggestion")
            surfaced = sev == "blocking" or (
                finding.get("verdict_kind") == "auto"
                and SEVERITY_RANK.get(sev, 0) >= floor
            )
            if surfaced:
                shown.append(
                    f"  [{sev}] {finding.get('rule_id')} L{finding.get('line')}: {finding.get('message')}"
                )
            else:
                rolled[sev] = rolled.get(sev, 0) + 1
    if not shown and not rolled:
        return None
    lines = []
    if shown:
        lines.append("auditor flagged the file you just changed:")
        lines += shown
    if rolled:
        ordered = sorted(rolled.items(), key=lambda kv: -SEVERITY_RANK.get(kv[0], 0))
        rollup = ", ".join(f"{n} {sev}" for sev, n in ordered)
        lines.append(f"+{rollup} lower — run /auditor:judge-findings")
    return "\n".join(lines)


def main() -> None:
    if os.environ.get("AUDITOR_AUTOHOOK") == "0" or not auditr_available():
        return
    event = read_event()
    if event is None:
        return
    file = changed_file(event)
    if file is None:
        return
    if os.environ.get("AUDITOR_AUTOHOOK_ASYNC") == "1":
        detach_incremental_scan(Path(event.get("cwd") or "."))
        return
    result = report(file)
    if result is None:
        return
    floor = SEVERITY_RANK.get(
        os.environ.get("AUDITOR_AUTOHOOK_SEVERITY", "high"), SEVERITY_RANK["high"]
    )
    summary = summarize(result, floor)
    if summary:
        emit_context("PostToolUse", summary)


if __name__ == "__main__":
    main()
