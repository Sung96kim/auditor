"""Writes the compact status cache the Claude Code plugin's status line reads.

Repo-wide only: written on directory scans, never on single-file `report`. The status line reads
this file (and nothing else) so it never has to shell out or open the index on its hot path.
"""

import json
import time
from pathlib import Path

from auditor.models import ScanResult

_TIERS = ("blocking", "high", "medium", "low", "suggestion")


def write_status(root: Path, results: list[ScanResult], *, configured: bool) -> Path:
    counts = {tier: 0 for tier in _TIERS}
    for r in results:
        for sev, n in r.counts.items():
            counts[sev.value] = counts.get(sev.value, 0) + n
    out = root / ".auditor" / ".status.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "severity": counts,
                    "configured": configured,
                    "written_at": int(time.time()),
                }
            )
        )
    except OSError:
        pass  # best-effort cache (gitignored) — a read-only fs must not fail the scan
    return out
