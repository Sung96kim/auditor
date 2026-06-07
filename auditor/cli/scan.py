"""``auditor scan`` — the workhorse. Audits a file or directory, with baseline / git-diff /
CI-gate / serve options, and a concise human summary by default (machine formats are opt-in).
"""

from pathlib import Path

import typer

from auditor.baseline import Baseline
from auditor.cli.apps import _status, app
from auditor.cli.helpers import _check_format, _emit, _fail, _require_exists, _run
from auditor.cli.options import (
    AllowLocalPlugins,
    BaselineFile,
    Changed,
    Exclude,
    FailOn,
    Format,
    IncludeGitignored,
    Incremental,
    MinSeverity,
    NoIndex,
    NoSkips,
    Output,
    PinRoot,
    Profile,
    ScanTarget,
    Serve,
    SeverityFilter,
    ShowIgnored,
    Since,
    StrictTests,
    Verbose,
    VsBase,
    WriteBaseline,
)
from auditor.cli.summary import print_summary
from auditor.config import load_config
from auditor.discovery import default_base_ref, find_root, git_changed_files
from auditor.engine import audit_target
from auditor.logconfig import configure as configure_logging
from auditor.models import (
    SEVERITIES_DESC,
    ScanResult,
    Severity,
    VerdictKind,
    severity_rank,
)
from auditor.reporters import render
from auditor.serve import ReportServer


def _severity_set(values: list[str]) -> set[str]:
    valid = {s.value for s in SEVERITIES_DESC}
    chosen = {v.lower() for v in values}
    unknown = chosen - valid
    if unknown:
        _fail(
            f"unknown severity {sorted(unknown)}; choose from {[s.value for s in SEVERITIES_DESC]}"
        )
    return chosen


def _check_severity(value: str) -> Severity:
    if value.lower() not in {s.value for s in SEVERITIES_DESC}:
        _fail(
            f"unknown severity '{value}'; choose from {[s.value for s in SEVERITIES_DESC]}"
        )
    return Severity(value.lower())


def _gate_tripped(results: list[ScanResult], fail_on: str) -> bool:
    # gate on *confirmed* (auto) findings only — candidates are "the agent should judge this" and
    # must not auto-break CI (otherwise a heuristic/candidate rule fails the build on benign code).
    floor = severity_rank(_check_severity(fail_on))
    return any(
        f.verdict_kind == VerdictKind.AUTO and severity_rank(f.severity) >= floor
        for r in results
        for f in r.findings
    )


def _filter_display(
    results: list[ScanResult], severity: list[str] | None, min_severity: str | None
) -> None:
    wanted = _severity_set(severity) if severity else None
    floor = severity_rank(_check_severity(min_severity)) if min_severity else None
    for r in results:
        if wanted is not None:
            r.findings = [f for f in r.findings if f.severity.value in wanted]
        if floor is not None:
            r.findings = [f for f in r.findings if severity_rank(f.severity) >= floor]


def _diff_report_only(
    target: Path,
    since: str | None,
    changed: bool,
    vs_base: bool,
    root: Path | None = None,
) -> set[str] | None:
    """Resolve the git diff ref (--since / --changed / --vs-base) and return the changed-file
    set to scope the output to, or None if no diff mode was requested. Exits cleanly on error."""
    root = root or find_root(target)
    ref: str | None = None
    if vs_base:
        ref = load_config(root).diff_base or default_base_ref(root)
        if ref is None:
            _fail(
                "no base branch found (tried main/master/develop/development); "
                "set [tool.auditor] diff_base or use --since <ref>"
            )
    elif since:
        ref = since
    elif changed:
        ref = "HEAD"
    if ref is None:
        return None
    try:
        report_only = git_changed_files(root, ref)
    except ValueError as exc:
        _fail(str(exc))
    if report_only is None:
        _fail("--since / --changed / --vs-base requires a git repository")
    return report_only


@app.command()
def scan(
    target: ScanTarget = Path("."),
    incremental: Incremental = False,
    no_index: NoIndex = False,
    strict_tests: StrictTests = False,
    allow_local_plugins: AllowLocalPlugins = False,
    profile: Profile = None,
    exclude: Exclude = None,
    no_skips: NoSkips = False,
    include_gitignored: IncludeGitignored = False,
    serve: Serve = False,
    output: Output = None,
    fmt: Format = None,
    severity: SeverityFilter = None,
    min_severity: MinSeverity = None,
    since: Since = None,
    changed: Changed = False,
    vs_base: VsBase = False,
    fail_on: FailOn = None,
    baseline: BaselineFile = None,
    write_baseline: WriteBaseline = None,
    root: PinRoot = None,
    show_ignored: ShowIgnored = False,
    verbose: Verbose = 0,
) -> None:
    """Audit a file or directory."""
    _require_exists(target)
    if fmt is not None:
        _check_format(fmt)  # fail fast on a bad --format, before the scan
    if verbose:
        configure_logging(verbose)

    report_only = _diff_report_only(target, since, changed, vs_base, root)
    if report_only is not None and not no_index:
        incremental = True  # whole-repo scan stays fast via the cache

    results = _run(
        audit_target(
            target,
            incremental=incremental,
            no_index=no_index,
            strict_tests=strict_tests,
            allow_local_plugins=allow_local_plugins,
            profile=profile,
            exclude=tuple(exclude or ()),
            no_skips=no_skips,
            include_gitignored=include_gitignored,
            report_only=report_only,
            root=root,
            show_ignored=show_ignored,
        ),
        f"auditing {target}…",
        spinner=not verbose,
    )

    if write_baseline is not None:
        recorded = Baseline.from_results(results).write(write_baseline)
        _status.print(
            f"[bold]Wrote baseline[/bold] {write_baseline} — {recorded} finding(s) recorded"
        )
        return

    hidden = 0
    if baseline is not None:
        if not baseline.exists():
            _fail(
                f"baseline file not found: {baseline} "
                f"(create it first with `scan --write-baseline {baseline}`)"
            )
        hidden = Baseline.load(baseline).filter(results)

    # baseline filtering runs before the gate, so a CI gate fails only on NEW findings
    gate_tripped = _gate_tripped(results, fail_on) if fail_on else False
    _filter_display(results, severity, min_severity)

    if serve:
        _serve_html(results)
        return
    # Default output is a concise human summary, not a raw machine dump — an agent that wants
    # parseable output asks for it explicitly with `-f json|md|sarif` (or `-o PATH`).
    if fmt is None and output is None:
        print_summary(results)
        if hidden:
            # human-only note; machine formats keep stdout pure (no baseline chatter)
            _status.print(
                f"[dim]{hidden} pre-existing finding(s) hidden by baseline[/dim]"
            )
    else:
        _emit(render(results, fmt or "json"), output)
    if gate_tripped:
        raise typer.Exit(1)


def _serve_html(results: list[ScanResult]) -> None:
    server = ReportServer(render(results, "html"))
    _status.print(
        f"[bold]Serving audit report at[/bold] {server.url}  (Ctrl-C to stop)"
    )
    server.serve()
