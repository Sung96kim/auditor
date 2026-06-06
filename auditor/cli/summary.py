"""The default ``auditor scan`` output: a compact, human-readable roll-up printed to stdout.

Machine formats (json/sarif/md/html) are opt-in via ``-f``/``-o``, so this never has to be
parseable — it's purely the interactive terminal view."""

from typing import NamedTuple

from rich.console import Console

from auditor.models import SEVERITIES_DESC, ScanResult, Severity

_out = Console()  # stdout — the interactive human summary
_SEV_STYLE = {
    "blocking": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "cyan",
    "suggestion": "bright_black",
}


class _Stats(NamedTuple):
    totals: dict[Severity, int]
    findings: int
    files_with: int
    suppressed: int
    ignored: int
    cached: int


def _summary_stats(results: list[ScanResult]) -> _Stats:
    totals = {s: 0 for s in SEVERITIES_DESC}
    for r in results:
        for sev, n in r.counts.items():
            totals[sev] += n
    return _Stats(
        totals=totals,
        findings=sum(totals.values()),
        files_with=sum(1 for r in results if r.findings),
        suppressed=sum(r.suppressed for r in results),
        ignored=sum(r.ignored for r in results),
        cached=sum(1 for r in results if r.cached),
    )


def _severity_line(totals: dict[Severity, int]) -> str:
    return "   ".join(
        f"[{_SEV_STYLE[s.value]}]{s.value} {totals[s]}[/{_SEV_STYLE[s.value]}]"
        for s in SEVERITIES_DESC
        if totals[s]
    )


def _meta_line(stats: _Stats) -> str:
    parts = (
        f"{stats.cached} cached" if stats.cached else "",
        f"{stats.suppressed} suppressed by skip" if stats.suppressed else "",
    )
    return " · ".join(p for p in parts if p)


def _ignored_note(stats: _Stats) -> str:
    return f" [dim]({stats.ignored} ignored)[/dim]" if stats.ignored else ""


def print_summary(results: list[ScanResult]) -> None:
    stats = _summary_stats(results)
    if not stats.findings:
        _out.print(
            f"[green]✓ clean[/green] — {len(results)} files, no findings"
            + _ignored_note(stats)
        )
        return

    _out.print(
        f"[bold]{stats.findings}[/bold] findings in [bold]{stats.files_with}[/bold] "
        f"of {len(results)} files" + _ignored_note(stats)
    )
    _out.print("  " + _severity_line(stats.totals))
    meta = _meta_line(stats)
    if meta:
        _out.print(f"  [dim]{meta}[/dim]")

    _out.print("\n[bold]worst files[/bold]")
    for r in sorted(results, key=lambda r: len(r.findings), reverse=True)[:5]:
        if r.findings:
            _out.print(f"  [red]{len(r.findings):>3}[/red]  {r.file}")

    _out.print(
        "\n[dim]-f json|md|sarif or -o PATH for the full report · -v/-vv/-vvv to log as it scans[/dim]"
    )
