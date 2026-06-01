"""Compact, stable JSON reporter — the default agent-facing output."""

import json
from typing import ClassVar

from auditor.models import Finding, ScanResult
from auditor.reporters.base import Reporter


def payload(results: list[ScanResult]) -> dict:
    """Structured result payload (used by the JSON reporter and the MCP server)."""
    return {"files": [_file_payload(r) for r in results], "totals": _totals(results)}


class JsonReporter(Reporter):
    format: ClassVar[str] = "json"

    def render(self, results: list[ScanResult]) -> str:
        return json.dumps(payload(results), indent=2)


def _file_payload(r: ScanResult) -> dict:
    return {
        "file": r.file,
        "language": r.language,
        "role": r.role.value,
        "cached": r.cached,
        "counts": {s.value: n for s, n in r.counts.items()},
        "findings": [_finding_payload(f) for f in r.findings],
        "skipped_rules": [
            {"rule_id": s.rule_id, "reason": s.reason} for s in r.skipped_rules
        ],
    }


def _finding_payload(f: Finding) -> dict:
    return {
        "rule_id": f.rule_id,
        "category": str(f.category),
        "severity": f.severity.value,
        "verdict_kind": f.verdict_kind.value,
        "line": f.line,
        "message": f.message,
        "evidence": f.evidence,
        "suggestion": f.suggestion,
        "checklist_item": f.checklist_item,
        "standard_refs": list(f.standard_refs),
    }


def _totals(results: list[ScanResult]) -> dict:
    totals: dict[str, int] = {}
    for r in results:
        for sev, n in r.counts.items():
            totals[sev.value] = totals.get(sev.value, 0) + n
    return totals
