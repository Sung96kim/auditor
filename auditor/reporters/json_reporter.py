"""Compact, stable JSON reporter — the default agent-facing output."""

# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (the JSON payload dicts ARE the wire format)

import json
from typing import ClassVar

from auditor.models import Finding, ScanResult
from auditor.reporters.base import Reporter


def payload(results: list[ScanResult], *, detail: str = "full") -> dict:
    """Structured result payload. ``detail``: 'full' (default; the CLI/back-compat shape),
    'compact' (rule metadata hoisted into a one-time `rules` map, findings slimmed, evidence
    dropped, clean files omitted — `scanned` carries the total count), or 'summary' (totals +
    per-rule/per-file counts, no findings). Files are ordered worst-severity-first so an agent
    reads the critical ones first."""
    ordered = sorted(results, key=lambda r: (not r.findings, r.severity_key))
    if detail == "summary":
        return _summary_payload(ordered)
    if detail == "compact":
        rules = _rules_map(ordered)
        return {
            "rules": rules,
            "files": [_compact_file_payload(r, rules) for r in ordered if r.findings],
            "totals": _totals(ordered),
            "scanned": len(ordered),
        }
    return {"files": [_file_payload(r) for r in ordered], "totals": _totals(ordered)}


def _rules_map(results: list[ScanResult]) -> dict:
    """rule_id -> the class-level constant fields (deduped once). `severity` is NOT here: config
    /role can relax a rule per file, so it stays inline on each finding."""
    out: dict[str, dict] = {}
    for r in results:
        for f in r.findings:
            if f.rule_id not in out:
                out[f.rule_id] = {
                    "category": str(f.category),
                    "verdict_kind": f.verdict_kind.value,
                    "checklist_item": f.checklist_item,
                    "standard_refs": list(f.standard_refs),
                    "suggestion": f.suggestion,
                }
    return out


def _compact_file_payload(r: ScanResult, rules: dict) -> dict:
    # no `counts`: each finding carries its severity, so a per-file rollup is derivable
    return {
        "file": r.file,
        "role": r.role.value,
        "findings": [_compact_finding(f, rules) for f in r.findings],
    }


def _compact_finding(f: Finding, rules: dict) -> dict:
    out: dict = {
        "rule_id": f.rule_id,
        "severity": f.severity.value,
        "line": f.line,
        "message": f.message,
    }
    if f.suggestion != rules[f.rule_id]["suggestion"]:
        out["suggestion"] = f.suggestion
    return out


def _summary_payload(results: list[ScanResult]) -> dict:
    by_rule: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for r in results:
        if r.findings:
            by_file[r.file] = len(r.findings)
        for f in r.findings:
            by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
    return {"totals": _totals(results), "by_rule": by_rule, "by_file": by_file}


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
        "suppressed": r.suppressed,
        "ignored": r.ignored,
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
    totals["suppressed"] = sum(r.suppressed for r in results)
    totals["ignored"] = sum(r.ignored for r in results)
    return totals
