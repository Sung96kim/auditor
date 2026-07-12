"""``auditor scan`` — the workhorse. Audits a file or directory, with baseline / git-diff /
CI-gate / serve options, and a concise human summary by default (machine formats are opt-in).
"""

from pathlib import Path

import typer
from pydantic import ValidationError

from auditor.baseline import Baseline
from auditor.cli.apps import app
from auditor.cli.console import err_console
from auditor.cli.helpers import (
    check_format,
    emit,
    fail,
    format_config_error,
    parse_config_json,
    require_exists,
    run_live,
    suggest,
)
from auditor.cli.options import (
    AllowLocalPlugins,
    BaselineFile,
    Changed,
    ConfigJson,
    Exclude,
    FailOn,
    Format,
    IncludeGitignored,
    Incremental,
    Isolated,
    Malware,
    MinSeverity,
    NoIndex,
    NoSkips,
    Output,
    PinRoot,
    Profile,
    RuleFilter,
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
from auditor.config import is_configured, load_config
from auditor.discovery import default_base_ref, find_root, git_changed_files
from auditor.engine import audit_target
from auditor.gate import check_severity as _check_severity
from auditor.gate import gate_tripped as _gate_tripped
from auditor.logconfig import configure as configure_logging
from auditor.malware.tools import resolve_tool
from auditor.models import (
    SEVERITIES_DESC,
    ScanResult,
    severity_rank,
)
from auditor.registry import REGISTRY
from auditor.reporters import render
from auditor.serve import ReportServer
from auditor.status import write_status


def _severity_set(values: list[str]) -> set[str]:
    valid = {s.value for s in SEVERITIES_DESC}
    chosen = {v.lower() for v in values}
    unknown = chosen - valid
    if unknown:
        fail(
            f"unknown severity {sorted(unknown)}; choose from {[s.value for s in SEVERITIES_DESC]}"
        )
    return chosen


def _floor(value: str) -> int:
    try:
        return severity_rank(_check_severity(value))
    except ValueError as exc:
        fail(str(exc))


def _rule_set(values: list[str]) -> set[str]:
    known = REGISTRY.rule_ids()
    chosen = set(values)
    for rid in chosen:
        if rid not in known:
            fail(
                f"unknown rule {rid!r}.{suggest(rid, known)} "
                f"Run `auditor rules list` to see all rules."
            )
    return chosen


def _filter_display(
    results: list[ScanResult],
    severity: list[str] | None,
    min_severity: str | None,
    rule: list[str] | None,
) -> None:
    wanted = _severity_set(severity) if severity else None
    floor = _floor(min_severity) if min_severity else None
    rules = _rule_set(rule) if rule else None
    for r in results:
        if wanted is not None:
            r.findings = [f for f in r.findings if f.severity.value in wanted]
        if floor is not None:
            r.findings = [f for f in r.findings if severity_rank(f.severity) >= floor]
        if rules is not None:
            r.findings = [f for f in r.findings if f.rule_id in rules]


def _diff_report_only(
    target: Path,
    since: str | None,
    changed: bool,
    vs_base: bool,
    root: Path | None = None,
) -> set[str] | None:
    """Resolve the git diff ref (--since / --changed / --vs-base) and return the changed-file
    set to scope the output to, or None if no diff mode was requested. Exits cleanly on error.
    """
    root = root or find_root(target)
    ref: str | None = None
    if vs_base:
        ref = load_config(root).diff_base or default_base_ref(root)
        if ref is None:
            fail(
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
        fail(str(exc))
    if report_only is None:
        fail("--since / --changed / --vs-base requires a git repository")
    return report_only


@app.command()
def scan(
    target: ScanTarget = Path("."),
    incremental: Incremental = False,
    no_index: NoIndex = False,
    isolated: Isolated = False,
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
    rule: RuleFilter = None,
    since: Since = None,
    changed: Changed = False,
    vs_base: VsBase = False,
    fail_on: FailOn = None,
    baseline: BaselineFile = None,
    write_baseline: WriteBaseline = None,
    root: PinRoot = None,
    show_ignored: ShowIgnored = False,
    config_json: ConfigJson = None,
    malware: Malware = None,
    verbose: Verbose = 0,
) -> None:
    """Audit a file or directory."""
    require_exists(target)
    if fmt is not None:
        check_format(fmt)  # fail fast on a bad --format, before the scan
    configure_logging(verbose)

    report_only = _diff_report_only(target, since, changed, vs_base, root)
    root = root or find_root(target)
    if report_only is not None and not no_index:
        incremental = True  # whole-repo scan stays fast via the cache

    overrides = parse_config_json(config_json)
    if malware is not None:
        if malware and not (
            resolve_tool("clamdscan")
            or resolve_tool("clamscan")
            or resolve_tool("osv-scanner")
        ):
            fail(
                "malware scan requested but neither ClamAV nor osv-scanner is "
                "installed — run `auditor malware install`"
            )
        merged = dict(overrides or {})
        merged["malware_scan"] = {**merged.get("malware_scan", {}), "enabled": malware}
        overrides = merged
    # "." renders as ".…" against the ellipsis — show the directory's name instead.
    target_label = target.resolve().name if str(target) == "." else target
    try:
        results = run_live(
            lambda report: audit_target(
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
                config_overrides=overrides,
                show_ignored=show_ignored,
                cross_file=not isolated,
                progress=report,
            ),
            f"auditing {target_label}",
            spinner=not verbose,
        )
    except ValidationError as exc:
        fail(f"invalid config — {format_config_error(exc)}")

    if write_baseline is not None:
        recorded = Baseline.from_results(results).write(write_baseline)
        err_console.print(
            f"[bold]Wrote baseline[/bold] {write_baseline} — {recorded} finding(s) recorded"
        )
        return

    if target.is_dir():
        write_status(root, results, configured=is_configured(root))

    hidden = 0
    if baseline is not None:
        if not baseline.exists():
            fail(
                f"baseline file not found: {baseline} "
                f"(create it first with `scan --write-baseline {baseline}`)"
            )
        hidden = Baseline.load(baseline).filter(results)

    # baseline filtering runs before the gate, so a CI gate fails only on NEW findings
    try:
        gate_tripped = _gate_tripped(results, fail_on) if fail_on else False
    except ValueError as exc:
        fail(str(exc))
    _filter_display(results, severity, min_severity, rule)

    if serve:
        _serve_html(results)
        return
    # Default output is a concise human summary, not a raw machine dump — an agent that wants
    # parseable output asks for it explicitly with `-f json|md|sarif` (or `-o PATH`).
    if fmt is None and output is None:
        print_summary(results)
        if hidden:
            # human-only note; machine formats keep stdout pure (no baseline chatter)
            err_console.print(
                f"[dim]{hidden} pre-existing finding(s) hidden by baseline[/dim]"
            )
    else:
        emit(render(results, fmt or "json"), output)
    if gate_tripped:
        raise typer.Exit(1)


def _serve_html(results: list[ScanResult]) -> None:
    server = ReportServer(render(results, "html"))
    err_console.print(
        f"[bold]Serving audit report at[/bold] {server.url}  (Ctrl-C to stop)"
    )
    server.serve()
