"""Compact, stable JSON reporter — the default agent-facing output."""

# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (the JSON payload dicts ARE the wire format)

import json
from collections.abc import Iterable
from typing import ClassVar

from auditor.models import Finding, ScanResult, severity_rank
from auditor.reporters.base import Reporter

_OMITTED_HINT = (
    "worst findings shown first; narrow with severity=/rule=, raise limit=, "
    "or fetch the complete report with detail='full'"
)

# (result, kept findings, whether the file was truncated by the cap)
Selection = list[tuple[ScanResult, list[Finding], bool]]


class JsonPayload:
    """Builds the structured, agent-facing result payload from a scan.

    ``detail`` picks the shape: ``full`` (every field inline — the CLI/back-compat shape),
    ``compact`` (rule metadata hoisted into a one-time ``rules`` map, findings slimmed, evidence
    dropped, clean files omitted — ``scanned`` carries the total count), or ``summary`` (totals
    plus per-rule/per-file counts, no findings). ``limit`` (compact only) caps the payload to the
    most-severe N findings, rolling the surplus into ``omitted``. Files are ordered
    worst-severity-first so the agent reads the critical ones first.
    """

    def __init__(
        self,
        results: list[ScanResult],
        *,
        detail: str = "full",
        limit: int | None = None,
    ) -> None:
        self.results = sorted(results, key=lambda r: (not r.findings, r.severity_key))
        self.detail = detail
        self.limit = limit

    def build(self) -> dict:
        if self.detail == "summary":
            return self.summary()
        if self.detail == "compact":
            return self.compact()
        return self.full()

    # --- shapes -------------------------------------------------------------------------

    def full(self) -> dict:
        return {
            "files": [self.full_file(r) for r in self.results],
            "totals": self.totals(),
        }

    def summary(self) -> dict:
        by_rule: dict[str, int] = {}
        by_file: dict[str, int] = {}
        for r in self.results:
            if r.findings:
                by_file[r.file] = len(r.findings)
            for f in r.findings:
                by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
        return {"totals": self.totals(), "by_rule": by_rule, "by_file": by_file}

    def compact(self) -> dict:
        selection, omitted_findings, omitted_files = self.select()
        rules = self.rules_map(f for _r, found, _t in selection for f in found)
        files = []
        for r, found, truncated in selection:
            entry = {
                "file": r.file,
                "role": r.role.value,
                "findings": [self.compact_finding(f, rules) for f in found],
            }
            if truncated:
                entry["truncated"] = True
            files.append(entry)
        out: dict = {
            "rules": rules,
            "files": files,
            "totals": self.totals(),
            "scanned": len(self.results),
        }
        if omitted_findings or omitted_files:
            out["omitted"] = {
                "findings": omitted_findings,
                "files": omitted_files,
                "hint": _OMITTED_HINT,
            }
        return out

    # --- compact selection & entries ----------------------------------------------------

    def kept_indices(self, dirty: list[ScanResult]) -> tuple[set[tuple[int, int]], int]:
        """The (result, finding) indices that survive the ``limit`` cap — all of them, or the
        most-severe N so a blocker is never dropped in favour of a low — plus the omitted count."""
        flat = [(ri, fi) for ri, r in enumerate(dirty) for fi in range(len(r.findings))]
        if self.limit is None or len(flat) <= self.limit:
            return set(flat), 0
        ranked = sorted(
            flat,
            key=lambda t: (
                -severity_rank(dirty[t[0]].findings[t[1]].severity),
                t[0],
                t[1],
            ),
        )
        keep = set(ranked[: self.limit])
        return keep, len(flat) - len(keep)

    def select(self) -> tuple[Selection, int, int]:
        """The capped findings grouped by file (worst files first, natural line order within a
        file), alongside the omitted finding/file counts."""
        dirty = [r for r in self.results if r.findings]
        keep, omitted_findings = self.kept_indices(dirty)
        selection: Selection = []
        omitted_files = 0
        for ri, r in enumerate(dirty):
            found = [f for fi, f in enumerate(r.findings) if (ri, fi) in keep]
            if not found:
                omitted_files += 1
                continue
            selection.append((r, found, len(found) < len(r.findings)))
        return selection, omitted_findings, omitted_files

    def rules_map(self, findings: Iterable[Finding]) -> dict:
        """rule_id -> the class-level constant fields (deduped once). ``severity`` is NOT here:
        config/role can relax a rule per file, so it stays inline on each finding."""
        out: dict[str, dict] = {}
        for f in findings:
            if f.rule_id not in out:
                out[f.rule_id] = {
                    "category": str(f.category),
                    "verdict_kind": f.verdict_kind.value,
                    "checklist_item": f.checklist_item,
                    "standard_refs": list(f.standard_refs),
                    "suggestion": f.suggestion,
                }
        return out

    def compact_finding(self, f: Finding, rules: dict) -> dict:
        out: dict = {
            "rule_id": f.rule_id,
            "severity": f.severity.value,
            "line": f.line,
            "message": f.message,
        }
        if f.suggestion != rules[f.rule_id]["suggestion"]:
            out["suggestion"] = f.suggestion
        return out

    # --- full entries -------------------------------------------------------------------

    def full_file(self, r: ScanResult) -> dict:
        return {
            "file": r.file,
            "language": r.language,
            "role": r.role.value,
            "cached": r.cached,
            "counts": {s.value: n for s, n in r.counts.items()},
            "suppressed": r.suppressed,
            "ignored": r.ignored,
            "findings": [self.full_finding(f) for f in r.findings],
            "skipped_rules": [
                {"rule_id": s.rule_id, "reason": s.reason} for s in r.skipped_rules
            ],
        }

    def full_finding(self, f: Finding) -> dict:
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

    def totals(self) -> dict:
        totals: dict[str, int] = {}
        for r in self.results:
            for sev, n in r.counts.items():
                totals[sev.value] = totals.get(sev.value, 0) + n
        totals["suppressed"] = sum(r.suppressed for r in self.results)
        totals["ignored"] = sum(r.ignored for r in self.results)
        return totals


def payload(
    results: list[ScanResult], *, detail: str = "full", limit: int | None = None
) -> dict:
    """Stable public entry point (used by the MCP tools) over :class:`JsonPayload`."""
    return JsonPayload(results, detail=detail, limit=limit).build()


class JsonReporter(Reporter):
    format: ClassVar[str] = "json"

    def render(self, results: list[ScanResult]) -> str:
        return json.dumps(JsonPayload(results).build(), indent=2)
