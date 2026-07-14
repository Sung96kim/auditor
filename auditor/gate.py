"""Shared CI-gate logic, used by both the CLI (`scan --fail-on`) and the MCP `scan` tool.

The gate counts *confirmed* (auto) findings only — candidates are "the agent should judge this"
and must never auto-break CI."""

from auditor.models import (
    SEVERITIES_DESC,
    ScanResult,
    Severity,
    VerdictKind,
    severity_rank,
)


def check_severity(value: str) -> Severity:
    if value.lower() not in {s.value for s in SEVERITIES_DESC}:
        raise ValueError(
            f"unknown severity '{value}'; choose from {[s.value for s in SEVERITIES_DESC]}"
        )
    return Severity(value.lower())


def gate_tripped(results: list[ScanResult], fail_on: str) -> bool:
    floor = severity_rank(check_severity(fail_on))
    return any(
        f.verdict_kind == VerdictKind.AUTO and severity_rank(f.severity) >= floor
        for r in results
        for f in r.findings
    )
