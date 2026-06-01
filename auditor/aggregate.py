"""Roll up the index into a single AUDIT.md (replaces the prior ad-hoc /tmp/aggregate.py).

Reads cached findings from the index — no re-scan — so `auditor aggregate` is cheap and
reflects the last scan of the registered scope.
"""

from pathlib import Path

from auditor.index import IndexStore
from auditor.models import Finding, IndexEntry, Severity, severity_rank


class AuditAggregator:
    """Builds the consolidated AUDIT.md from the index for a scope."""

    def __init__(self, index: IndexStore) -> None:
        self.index = index

    async def markdown(self) -> str:
        files = await self.index.files()
        findings = await self.index.all_findings()
        return _render(files, findings)

    async def write(self, out_path: Path) -> Path:
        out_path.write_text(await self.markdown())
        return out_path


def _render(files: list[IndexEntry], findings: list[Finding]) -> str:
    totals = _totals(files)
    flagged = sorted(
        (e for e in files if sum(e.counts.values()) > 0),
        key=lambda e: -sum(e.counts.values()),
    )

    lines = [
        "# Audit — consolidated report",
        "",
        f"Scope: {len(files)} files audited.",
        "",
        (
            f"**Totals — blocking: {totals[Severity.BLOCKING]} · high: {totals[Severity.HIGH]} · "
            f"medium: {totals[Severity.MEDIUM]} · low: {totals[Severity.LOW]}**"
        ),
        "",
        "## Files with findings (most severe first)",
        "",
        "| File | Role | Blocking | High | Medium | Low |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if flagged:
        for e in flagged:
            c = e.counts
            lines.append(
                f"| `{e.path}` | {e.role.value} | {c.get('blocking', 0)} | "
                f"{c.get('high', 0)} | {c.get('medium', 0)} | {c.get('low', 0)} |"
            )
    else:
        lines.append("| _(none)_ | | 0 | 0 | 0 | 0 |")
    lines.append("")

    candidates = [f for f in findings if f.verdict_kind.value == "candidate"]
    if candidates:
        lines += ["## Candidates to judge", ""]
        for f in sorted(candidates, key=lambda f: (-severity_rank(f.severity), f.rule_id)):
            lines.append(f"- **{f.severity.value}** `{f.rule_id}` — {f.message}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _totals(files: list[IndexEntry]) -> dict[Severity, int]:
    totals = {s: 0 for s in Severity}
    for entry in files:
        for sev, n in entry.counts.items():
            totals[Severity(sev)] += n
    return totals
