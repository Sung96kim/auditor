"""Roll up the index into a single AUDIT.md (replaces the prior ad-hoc /tmp/aggregate.py).

Reads cached findings from the index — no re-scan — so `auditor aggregate` is cheap and
reflects the last scan of the registered scope. Persistent ignores are applied here too, so the
consolidated report matches what `scan` shows.
"""

from pathlib import Path

from auditor.database import IndexStore
from auditor.ignores import IgnoreList
from auditor.models import FileRole, ScanResult, Severity, severity_rank


class AuditAggregator:
    """Builds the consolidated AUDIT.md from the index for a scope."""

    def __init__(self, index: IndexStore) -> None:
        self.index = index

    async def _results(self) -> list[ScanResult]:
        """Reconstruct per-file results from the index and drop ignored findings."""
        entries = await self.index.files.list()
        grouped = await self.index.findings.findings_grouped()
        results = [
            ScanResult(
                file=e.path,
                language=e.language,
                role=FileRole(e.role),
                findings=grouped.get(e.path, []),
            )
            for e in entries
        ]
        IgnoreList.from_rows(await self.index.ignores.list()).filter(results)
        return results

    async def markdown(self) -> str:
        return _render(await self._results())

    async def write(self, out_path: Path) -> Path:
        out_path.write_text(await self.markdown())
        return out_path


def _render(results: list[ScanResult]) -> str:
    totals = {s: 0 for s in Severity}
    for r in results:
        for sev, n in r.counts.items():
            totals[sev] += n
    flagged = sorted(
        (r for r in results if r.findings),
        key=lambda r: -len(r.findings),
    )

    lines = [
        "# Audit — consolidated report",
        "",
        f"Scope: {len(results)} files audited.",
        "",
        (
            f"**Totals — blocking: {totals[Severity.BLOCKING]} · high: {totals[Severity.HIGH]} · "
            f"medium: {totals[Severity.MEDIUM]} · low: {totals[Severity.LOW]} · "
            f"suggestion: {totals[Severity.SUGGESTION]}**"
        ),
        "",
        "## Files with findings (most severe first)",
        "",
        "| File | Role | Blocking | High | Medium | Low |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if flagged:
        for r in flagged:
            c = r.counts
            lines.append(
                f"| `{r.file}` | {r.role.value} | {c[Severity.BLOCKING]} | "
                f"{c[Severity.HIGH]} | {c[Severity.MEDIUM]} | {c[Severity.LOW]} |"
            )
    else:
        lines.append("| _(none)_ | | 0 | 0 | 0 | 0 |")
    lines.append("")

    candidates = [
        f for r in results for f in r.findings if f.verdict_kind.value == "candidate"
    ]
    if candidates:
        lines += ["## Candidates to judge", ""]
        for f in sorted(
            candidates, key=lambda f: (-severity_rank(f.severity), f.rule_id)
        ):
            lines.append(f"- **{f.severity.value}** `{f.rule_id}` — {f.message}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
