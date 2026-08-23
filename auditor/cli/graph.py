"""``auditor graph`` — the semantic-graph command group; ``auditr graph --help`` lists the set.

Imported on the first ``graph`` subcommand by ``cli/lazy.py``, so the rest of the CLI never pays
this module's numpy/scikit-learn/networkx import.
"""

import shutil
import subprocess
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Annotated, Any

import typer

from auditor.cli.console import ACCENT, err_console
from auditor.cli.graph_refine import register as register_refine
from auditor.cli.helpers import (
    fail,
    open_index,
    present,
    run,
    run_staged,
    warn_unknown_config,
)
from auditor.cli.lazy import GRAPH_HELP
from auditor.cli.options import (
    ExportDepth,
    FlowDepth,
    FlowExpandHubs,
    FlowIn,
    FlowIncludeTests,
    FlowKinds,
    FlowLimit,
    FlowStopAt,
    FlowSymbol,
    GraphTarget,
)
from auditor.cli.render import (
    render_graph_build,
    render_graph_clusters,
    render_graph_concept,
    render_graph_flow,
    render_graph_neighbors,
    render_graph_related,
    render_graph_search,
    render_graph_usages,
)
from auditor.config import AuditorSettings, load_config, unknown_repo_keys
from auditor.database import IndexStore
from auditor.discovery import find_root
from auditor.engine import audit_target
from auditor.graph import GRAPH_OVERRIDE
from auditor.graph.build import GraphBuilder
from auditor.graph.flow import FlowDirection, FlowOptions
from auditor.graph.model import DEFAULT_FLOW_LIMIT, EdgeKind
from auditor.graph.query import GraphQuery
from auditor.graph.refine.lock import rebuild_lock
from auditor.graph.viz import build_payload, render_app, to_dot
from auditor.paths import index_db_path, repo_key
from auditor.serve import ReportServer

graph_app = typer.Typer(no_args_is_help=True, help=GRAPH_HELP)


async def _autoscan(root: Path) -> None:
    """Incremental scan with graph extraction forced on."""
    await audit_target(root, incremental=True, config_overrides=GRAPH_OVERRIDE)


async def _build(
    root: Path,
    settings: AuditorSettings,
    progress: Callable[[str], None] | None = None,
    *,
    lock_held: bool = False,
) -> dict:
    """Rebuild ``root``'s graph. ``lock_held`` when the caller already took the rebuild lock, so
    a clear plus a rescan plus this build stay one hold."""
    async with await open_index(root) as index:
        await index.repos.register(time.time())
        return await GraphBuilder().rebuild(
            index, settings, progress=progress, lock_held=lock_held
        )


@graph_app.command("build")
def graph_build(
    target: GraphTarget = Path("."),
    no_scan: bool = typer.Option(
        False,
        "--no-scan",
        help="Skip auto-scan; build from existing cached facts only.",
    ),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="Discard cached graph facts and re-extract from scratch. Facts are keyed by file "
        "content, so use this after upgrading auditor to pick up extractor changes.",
    ),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Build the semantic graph, auto-scanning to extract facts first (use --no-scan to skip)."""
    if rebuild and no_scan:
        raise typer.BadParameter(
            "--rebuild discards the cached facts, so --no-scan would build an empty graph and "
            "clear the queue with it. Drop one of them."
        )
    root = find_root(target)
    warn_unknown_config(unknown_repo_keys(root))
    settings = load_config(root)

    async def do_build(report: Callable[[str], None]) -> dict:
        async with await open_index(root) as index:
            identity = index.partition.identity
        # one hold across clear, scan and build: a build landing on the half-rescanned graph
        # cannot tell a file being re-extracted from a symbol that was deleted
        async with rebuild_lock(
            identity,
            waiting=lambda: report("waiting for the observer's rebuild"),
            poll=settings.graph.rebuild_lock_poll_seconds,
        ):
            if rebuild:
                report("clearing cached facts…")
                async with await open_index(root) as index:
                    await index.graph.clear_facts()
            if not no_scan:
                report("scanning repository…")
                await _autoscan(root)
            report("building graph…")
            return await _build(root, settings, report, lock_held=True)

    present(run_staged(do_build, "building graph…"), render_graph_build, as_json=json_)


def _query_cmd(
    fn_name: str,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    async def runner(root: Path, **kw: Any) -> Any:
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            return await getattr(GraphQuery(index), fn_name)(**kw)

    return runner


@graph_app.command("related")
def graph_related(
    symbol: str,
    target: GraphTarget = Path("."),
    limit: int = 10,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Top semantic neighbors of a symbol (name + usage), ranked."""
    root = find_root(target)
    present(
        run(_query_cmd("related")(root, symbol=symbol, limit=limit), "querying…"),
        render_graph_related,
        as_json=json_,
    )


@graph_app.command("neighbors")
def graph_neighbors(
    symbol: str,
    target: GraphTarget = Path("."),
    depth: int = 1,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Structural neighbors (calls/overrides/...) up to a depth."""
    root = find_root(target)
    present(
        run(_query_cmd("neighbors")(root, symbol=symbol, depth=depth), "querying…"),
        render_graph_neighbors,
        as_json=json_,
    )


@graph_app.command("concept")
def graph_concept(
    term: str,
    target: GraphTarget = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Symbols in the concept cluster matching a term."""
    root = find_root(target)
    present(
        run(_query_cmd("concept")(root, term=term), "querying…"),
        render_graph_concept,
        as_json=json_,
    )


@graph_app.command("clusters")
def graph_clusters(
    target: GraphTarget = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List concept clusters (label + size)."""
    root = find_root(target)
    present(
        run(_query_cmd("clusters")(root), "querying…"),
        render_graph_clusters,
        as_json=json_,
    )


@graph_app.command("search")
def graph_search(
    term: str,
    target: GraphTarget = Path("."),
    limit: int = 20,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Find symbols whose id contains the term (highest-rank first)."""
    root = find_root(target)
    present(
        run(_query_cmd("search")(root, term=term, limit=limit), "searching…"),
        render_graph_search,
        as_json=json_,
    )


@graph_app.command("usages")
def graph_usages(
    symbol: str,
    target: GraphTarget = Path("."),
    sample: int = 5,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """How a symbol is used/connected: edges grouped by kind with full counts (used_by vs
    depends_on)."""
    root = find_root(target)
    present(
        run(_query_cmd("usages")(root, symbol=symbol, sample=sample), "querying…"),
        render_graph_usages,
        as_json=json_,
    )


def _split_kinds(raw: str | None) -> list[str]:
    """Parse --kinds, rejecting a typo rather than returning a tree that silently omits it."""
    kinds = [k.strip() for k in (raw or "").split(",") if k.strip()]
    allowed = sorted(e.value for e in EdgeKind)
    unknown = [k for k in kinds if k not in allowed]
    if unknown:
        raise typer.BadParameter(
            f"unknown --kinds: {', '.join(unknown)}. Valid: {', '.join(allowed)}"
        )
    return kinds


@graph_app.command("flow")
def graph_flow(
    symbol: str,
    target: GraphTarget = Path("."),
    inbound: FlowIn = False,
    depth: FlowDepth = 4,
    limit: FlowLimit = DEFAULT_FLOW_LIMIT,
    kinds: FlowKinds = None,
    include_tests: FlowIncludeTests = False,
    expand_hubs: FlowExpandHubs = False,
    stop_at: FlowStopAt = None,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Read a code path from a symbol: what it calls, or with --in what reaches it."""
    root = find_root(target)
    options = FlowOptions(
        direction=FlowDirection.IN if inbound else FlowDirection.OUT,
        depth=depth,
        limit=limit,
        kinds=tuple(_split_kinds(kinds)),
        include_tests=include_tests,
        expand_hubs=expand_hubs,
        stop_at=tuple(stop_at or ()),
        hub_fan_in=load_config(root).graph.flow_hub_fan_in,
    )
    present(
        run(_query_cmd("flow")(root, symbol=symbol, options=options), "tracing flow…"),
        render_graph_flow,
        as_json=json_,
    )


async def _serve_html(
    root: Path, *, rebuild: bool, report: Callable[[str], None]
) -> str:
    """Render the graph UI HTML. Reuses the already-built graph (fast) unless it's missing or
    ``rebuild`` is set — only then does it pay the scan + build cost."""
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        has_graph = bool(await index.graph.nodes())
    if rebuild or not has_graph:
        report("scanning repository…")
        await _autoscan(root)
        report("building graph…")
        await _build(root, load_config(root), report)
    report("preparing UI…")
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        return render_app(await build_payload(index))


@graph_app.command("serve")
def graph_serve(
    target: GraphTarget = Path("."),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="Re-scan and rebuild the graph before serving (use after code changes).",
    ),
    no_open: bool = typer.Option(
        False, "--no-open", help="Skip opening a browser tab."
    ),
) -> None:
    """Serve the interactive graph UI. Serves the already-built graph when present (fast); only
    scans + builds when it's missing. Pass --rebuild to force a fresh build."""
    root = find_root(target)
    html = run_staged(
        lambda report: _serve_html(root, rebuild=rebuild, report=report),
        "preparing graph UI…",
    )
    server = ReportServer(html)
    err_console.print(
        f"[{ACCENT}]◆[/] serving graph UI at [bold]{server.url}[/bold]  [dim](Ctrl-C to stop)[/dim]"
    )
    server.serve(open_browser=not no_open)


@graph_app.command("export")
def graph_export(
    target: GraphTarget = Path("."),
    fmt: Annotated[str, typer.Option("--format")] = "dot",
    cluster: str | None = None,
    symbol: str | None = None,
    depth: ExportDepth = None,
    flow: FlowSymbol = None,
    inbound: FlowIn = False,
) -> None:
    """Export a Graphviz DOT (or SVG via the system graphviz) of the graph/cluster/ego/flow."""
    root = find_root(target)
    if flow is not None and (symbol is not None or cluster is not None):
        raise typer.BadParameter("--flow cannot be combined with --symbol or --cluster")
    if symbol is not None and cluster is not None:
        raise typer.BadParameter("--symbol cannot be combined with --cluster")
    if inbound and flow is None:
        raise typer.BadParameter("--in only applies to --flow")

    async def do_export() -> str | None:
        """``None`` when --flow named a symbol the graph does not hold."""
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            payload = await build_payload(index)
            if flow is None:
                return to_dot(
                    payload,
                    cluster=cluster,
                    symbol=symbol,
                    depth=1 if depth is None else depth,
                )
            tree = await GraphQuery(index).flow(
                flow,
                FlowOptions(
                    direction=FlowDirection.IN if inbound else FlowDirection.OUT,
                    depth=4 if depth is None else depth,
                    hub_fan_in=load_config(root).graph.flow_hub_fan_in,
                ),
            )
        return to_dot(payload, flow=tree) if tree else None

    dot = run(do_export(), "exporting…")
    if dot is None:
        fail(f"no such symbol: {flow}")
    if fmt == "dot":
        typer.echo(dot)
        return
    if fmt == "svg":
        exe = shutil.which("dot")
        if not exe:
            raise typer.BadParameter(
                "graphviz `dot` not found; install graphviz or use --format dot"
            )
        out = subprocess.run(
            [exe, "-Tsvg"], input=dot, capture_output=True, text=True, check=True
        )
        typer.echo(out.stdout)
        return
    raise typer.BadParameter("--format must be dot or svg")


register_refine(graph_app)
