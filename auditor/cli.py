"""Command-line interface (typer). Commands emit compact JSON for agents; the async engine
is bridged with ``asyncio.run``.
"""

import ast
import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated, Any, NamedTuple, NoReturn, TypeVar

import typer
from rich.console import Console

from auditor import crossfile as crossfile_pass
from auditor.aggregate import AuditAggregator
from auditor.config import load_config
from auditor.discovery import FileDiscovery, find_root
from auditor.engine import ScanEngine, audit_target
from auditor.index import IndexStore
from auditor.languages.python.auditor import PythonAuditor
from auditor.logconfig import configure as configure_logging
from auditor.models import ManifestEntry, ScanResult, Severity, SEVERITIES_DESC
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY
from auditor.reporters import render
from auditor.roles import RoleClassifier
from auditor.serve import ReportServer

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="A token-efficient repo auditor."
)
index_app = typer.Typer(
    no_args_is_help=True, help="Manage the audit-scope index + cache."
)
config_app = typer.Typer(no_args_is_help=True, help="Inspect resolved configuration.")
rules_app = typer.Typer(no_args_is_help=True, help="Inspect detector rules.")
plugins_app = typer.Typer(no_args_is_help=True, help="Inspect loaded plugins.")
app.add_typer(index_app, name="index")
app.add_typer(config_app, name="config")
app.add_typer(rules_app, name="rules")
app.add_typer(plugins_app, name="plugins")


# Spinner goes to STDERR so it never corrupts the JSON/SARIF stdout that agents parse;
# rich auto-disables the animation when stderr isn't a TTY (piped/captured output).
_status = Console(stderr=True)
_out = Console()  # stdout — the interactive human summary

_SEV_STYLE = {
    "blocking": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "cyan",
    "suggestion": "bright_black",
}


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2))


def _fail(message: str) -> NoReturn:
    """Emit a clean one-line error to stderr and exit non-zero (no traceback)."""
    _status.print(f"[red]error:[/red] {message}")
    raise typer.Exit(1)


_T = TypeVar("_T")


def _run(
    coro: Coroutine[Any, Any, _T], message: str = "auditing…", *, spinner: bool = True
) -> _T:
    """Run an async core call. Shows a stderr spinner unless ``spinner`` is off (e.g. when
    ``-v`` logging is driving the progress output instead)."""
    if not spinner:
        return asyncio.run(coro)
    with _status.status(message, spinner="dots"):
        return asyncio.run(coro)


def _index_db(root: Path) -> Path:
    return root / ".auditor" / "index.db"


# --------------------------------------------------------------------- scan


@app.command()
def scan(
    target: Annotated[Path, typer.Argument(help="File or directory to audit.")] = Path(
        "."
    ),
    incremental: Annotated[
        bool, typer.Option("-i", "--incremental", help="Use/update the on-disk cache.")
    ] = False,
    no_index: Annotated[
        bool, typer.Option("-n", "--no-index", help="Force stateless (no cache).")
    ] = False,
    strict_tests: Annotated[
        bool,
        typer.Option("-t", "--strict-tests", help="Audit tests at production strength."),
    ] = False,
    allow_local_plugins: Annotated[
        bool,
        typer.Option("-a", "--allow-local-plugins", help="Load .auditor/plugins/*.py."),
    ] = False,
    profile: Annotated[
        str | None,
        typer.Option("-p", "--profile", help="Override the profile for this run: base|strict|pydantic|all-strict."),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("-x", "--exclude", help="Glob to ignore (repeatable), on top of config."),
    ] = None,
    no_noqa: Annotated[
        bool,
        typer.Option("--no-noqa", help="Ignore in-file noqa directives (un-silenceable sweep)."),
    ] = False,
    serve: Annotated[
        bool,
        typer.Option("-s", "--serve", help="Render HTML and open it in a browser on a local port."),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Write the report to this path instead of stdout."),
    ] = None,
    fmt: Annotated[
        str | None,
        typer.Option("-f", "--format", help="json | sarif | md | html. Default: a summary on a terminal, json when piped."),
    ] = None,
    verbose: Annotated[
        int,
        typer.Option("-v", "--verbose", count=True, help="Log progress to stderr: -v files, -vv detail, -vvv findings."),
    ] = 0,
) -> None:
    """Audit a file or directory."""
    if not target.exists():
        _fail(f"no such file or directory: {target}")
    if verbose:
        configure_logging(verbose)
    results = _run(
        audit_target(
            target,
            incremental=incremental,
            no_index=no_index,
            strict_tests=strict_tests,
            allow_local_plugins=allow_local_plugins,
            profile=profile,
            exclude=tuple(exclude or ()),
            no_noqa=no_noqa,
        ),
        f"auditing {target}…",
        spinner=not verbose,
    )
    if serve:
        _serve_html(results)
        return
    # Default output is a concise human summary, not a raw machine dump — an agent that wants
    # parseable output asks for it explicitly with `-f json|md|sarif` (or `-o PATH`).
    if fmt is None and output is None:
        _print_summary(results)
        return
    _emit(render(results, fmt or "json"), output)


def _serve_html(results: list[ScanResult]) -> None:
    server = ReportServer(render(results, "html"))
    _status.print(f"[bold]Serving audit report at[/bold] {server.url}  (Ctrl-C to stop)")
    server.serve()


def _emit(rendered: str, output: Path | None) -> None:
    """Write a rendered report to ``output`` (with a stderr note) or echo it to stdout."""
    if output is None:
        typer.echo(rendered)
        return
    output.write_text(rendered, encoding="utf-8")
    typer.echo(f"wrote {output}", err=True)


class _Stats(NamedTuple):
    totals: dict[Severity, int]
    findings: int
    files_with: int
    suppressed: int
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
        f"{stats.suppressed} suppressed by noqa" if stats.suppressed else "",
    )
    return " · ".join(p for p in parts if p)


def _print_summary(results: list[ScanResult]) -> None:
    """The default scan output: a compact, readable roll-up. Machine formats are opt-in via
    ``-f``/``-o``, so this never has to be parseable."""
    stats = _summary_stats(results)
    if not stats.findings:
        _out.print(f"[green]✓ clean[/green] — {len(results)} files, no findings")
        return

    _out.print(
        f"[bold]{stats.findings}[/bold] findings in [bold]{stats.files_with}[/bold] of {len(results)} files"
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


# --------------------------------------------------------------- manifest/report


@app.command()
def manifest(file: Annotated[Path, typer.Argument(help="Python file.")]) -> None:
    """Print the AST class+function manifest for one file (no detectors)."""
    if not file.is_file():
        _fail(f"no such file: {file}")
    tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
    entries = ManifestEntry.from_module(tree)
    _echo_json([e.model_dump(mode="json") for e in entries])


@app.command()
def report(
    file: Annotated[Path, typer.Argument(help="Python file.")],
    profile: Annotated[
        str | None,
        typer.Option("-p", "--profile", help="Override the profile for this run: base|strict|pydantic|all-strict."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Write the report to this path instead of stdout."),
    ] = None,
    fmt: Annotated[str, typer.Option("-f", "--format", help="json | sarif | md | html")] = "json",
) -> None:
    """Audit one file (stateless) — manifest + findings in one call."""
    if not file.is_file():
        _fail(f"no such file: {file}")
    result = _run(_report(file, profile), f"auditing {file.name}…")
    _emit(render([result], fmt), output)


async def _report(file: Path, profile: str | None) -> ScanResult:
    root = find_root(file)
    engine = ScanEngine(root, load_config(root, profile=profile))
    return await engine.scan_file(file)


# --------------------------------------------------------------- discover


@app.command()
def discover(
    target: Annotated[Path, typer.Argument()] = Path("."),
) -> None:
    """List auditable files with their classified role."""
    if not target.exists():
        _fail(f"no such file or directory: {target}")
    root = find_root(target)
    classifier = RoleClassifier(load_config(root).role_globs)
    out = []
    for path in FileDiscovery(root).files(target):
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        role = classifier.classify(
            rel, path.read_text(encoding="utf-8", errors="replace")
        )
        out.append({"file": rel, "role": role.value})
    _echo_json(out)


# --------------------------------------------------------------- aggregate


@app.command()
def aggregate(
    target: Annotated[Path, typer.Argument()] = Path("."),
    out: Annotated[
        Path, typer.Option("-o", "--out", help="Write AUDIT.md here.")
    ] = Path("AUDIT.md"),
) -> None:
    """Roll up the index into AUDIT.md (run `scan --incremental` first)."""
    root = find_root(target)
    path = _run(_aggregate(root, out), "aggregating…")
    typer.echo(f"wrote {path}")


async def _aggregate(root: Path, out: Path) -> Path:
    async with await IndexStore.connect(_index_db(root)) as index:
        return await AuditAggregator(index).write(out)


# --------------------------------------------------------------- crossfile


@app.command()
def crossfile(target: Annotated[Path, typer.Argument()] = Path(".")) -> None:
    """Recompute cross-file duplicate findings from the index."""
    root = find_root(target)
    count = _run(_crossfile(root), "cross-file pass…")
    _echo_json({"cross_file_findings": count})


async def _crossfile(root: Path) -> int:
    async with await IndexStore.connect(_index_db(root)) as index:
        per_file = await crossfile_pass.run(index)
        return sum(len(v) for v in per_file.values())


# --------------------------------------------------------------- index subcommands


@index_app.command("add")
def index_add(
    paths: Annotated[
        list[Path], typer.Argument(help="Files to register in the audit scope.")
    ],
    target: Annotated[Path, typer.Option("-r", "--root")] = Path("."),
) -> None:
    """Register files as the audit scope."""
    root = find_root(target)
    rels = [
        str(p.relative_to(root)) if p.is_relative_to(root) else str(p) for p in paths
    ]
    _run(_index_add(root, rels), "registering scope…")
    _echo_json({"added": rels})


async def _index_add(root: Path, rels: list[str]) -> None:
    async with await IndexStore.connect(_index_db(root)) as index:
        await index.add_scope(rels)


@index_app.command("list")
def index_list(target: Annotated[Path, typer.Option("-r", "--root")] = Path(".")) -> None:
    """List the registered scope + per-file counts."""
    root = find_root(target)
    _echo_json(_run(_index_list(root), "reading index…"))


async def _index_list(root: Path) -> list[dict]:
    async with await IndexStore.connect(_index_db(root)) as index:
        return [e.model_dump(mode="json") for e in await index.files()]


# --------------------------------------------------------------- config/rules/plugins


@config_app.command("show")
def config_show(target: Annotated[Path, typer.Option("-r", "--root")] = Path(".")) -> None:
    """Print the resolved configuration."""
    settings = load_config(find_root(target))
    _echo_json(settings.model_dump(mode="json"))


@rules_app.command("list")
def rules_list(
    category: Annotated[
        str | None, typer.Option("-c", "--category", help="Filter by category.")
    ] = None,
    standard: Annotated[
        str | None, typer.Option("-s", "--standard", help="bandit | owasp coverage.")
    ] = None,
) -> None:
    """List every registered detector rule."""
    rows = []
    for rid in sorted(REGISTRY.rule_ids()):
        det = REGISTRY.detector(rid)
        if category and str(det.category) != category:
            continue
        refs = list(det.standard_refs)
        if standard and not any(r.startswith(f"{standard}:") for r in refs):
            continue
        rows.append(
            {
                "rule_id": rid,
                "category": str(det.category),
                "default_severity": det.default_severity.value,
                "verdict_kind": det.verdict_kind.value,
                "standard_refs": refs,
                "source": REGISTRY.source_of("detector", rid),
            }
        )
    _echo_json(rows)


@plugins_app.command("list")
def plugins_list(target: Annotated[Path, typer.Option("-r", "--root")] = Path(".")) -> None:
    """Show every loaded detector/language/reporter/profile and its source."""
    loader = PluginLoader()
    load_config(find_root(target), loader=loader)
    payload = REGISTRY.snapshot()
    payload["warnings"] = loader.warnings
    _echo_json(payload)


# ensure all built-in languages register for discovery's suffix list
_ = PythonAuditor


if __name__ == "__main__":
    app()
