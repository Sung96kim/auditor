"""Markdown reporter — human-readable summary, also used for AUDIT.md rollups."""

from typing import ClassVar

from auditor.models import ScanResult, Severity, severity_rank
from auditor.reporters.base import Reporter


class MarkdownReporter(Reporter):
    format: ClassVar[str] = "md"

    def render(self, results: list[ScanResult]) -> str:
        totals = _totals(results)
        lines = ["# Audit report", ""]
        lines.append(_totals_line(totals))
        lines.append("")
        flagged = [r for r in results if r.findings]
        flagged.sort(key=lambda r: r.severity_key)
        if flagged:
            lines += [
                "## Files with findings",
                "",
                "| File | Role | Blocking | High | Medium | Low |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
            for r in flagged:
                c = r.counts
                lines.append(
                    f"| `{r.file}` | {r.role.value} | {c[Severity.BLOCKING]} | "
                    f"{c[Severity.HIGH]} | {c[Severity.MEDIUM]} | {c[Severity.LOW]} |"
                )
            lines.append("")
        for r in flagged:
            lines += [f"### `{r.file}`", ""]
            for f in sorted(
                r.findings, key=lambda f: (-severity_rank(f.severity), f.line)
            ):
                mark = "🔧" if f.verdict_kind.value == "auto" else "🔎"
                lines.append(
                    f"- {mark} **{f.severity.value}** `{f.rule_id}` (L{f.line}) — {f.message}"
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _totals(results: list[ScanResult]) -> dict[Severity, int]:
    out = {s: 0 for s in Severity}
    for r in results:
        for sev, n in r.counts.items():
            out[sev] += n
    return out


def _totals_line(totals: dict[Severity, int]) -> str:
    return (
        f"**Totals — blocking: {totals[Severity.BLOCKING]} · high: {totals[Severity.HIGH]} · "
        f"medium: {totals[Severity.MEDIUM]} · low: {totals[Severity.LOW]}**"
    )
