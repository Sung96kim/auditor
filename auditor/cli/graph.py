"""``auditor graph`` — semantic-graph commands: build|related|neighbors|concept|clusters|export.

Imported only via a guarded mount in cli/__init__, so the core CLI works without the [graph] extra.
"""

import shutil
import subprocess
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Annotated, Any

import typer

from auditor.cli.console import ACCENT, err_console
from auditor.cli.helpers import present, run, run_staged
from auditor.cli.render import (
    render_graph_build,
    render_graph_clusters,
    render_graph_concept,
    render_graph_neighbors,
    render_graph_related,
    render_graph_search,
    render_graph_usages,
)
from auditor.config import load_config
from auditor.database import IndexStore
from auditor.discovery import find_root
from auditor.engine import audit_target
from auditor.graph.build import GraphBuilder
from auditor.graph.query import GraphQuery
from auditor.graph.viz import build_payload, render_app, to_dot
from auditor.paths import index_db_path, repo_key
from auditor.serve import ReportServer

graph_app = typer.Typer(
    no_args_is_help=True, help="Build + query the semantic code graph."
)

_Target = Annotated[Path, typer.Argument(help="Repo root (default: .)")]


GRAPH_OVERRIDE: dict = {"graph": {"enabled": True}}


async def _autoscan(root: Path) -> None:
    """Incremental scan with graph extraction forced on."""
    await audit_target(root, incremental=True, config_overrides=GRAPH_OVERRIDE)


async def _build(root: Path, progress: Callable[[str], None] | None = None) -> dict:
    settings = load_config(root)
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        await index.repos.register(time.time())
        return await GraphBuilder().run(index, settings, progress=progress)


@graph_app.command("build")
def graph_build(
    target: _Target = Path("."),
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
    root = find_root(target)

    async def do_build(report: Callable[[str], None]) -> dict:
        if rebuild:
            report("clearing cached facts…")
            async with await IndexStore.connect(
                index_db_path(), repo_key(root)
            ) as index:
                await index.graph.clear_facts()
        if not no_scan:
            report("scanning repository…")
            await _autoscan(root)
        report("building graph…")
        return await _build(root, report)

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
    target: _Target = Path("."),
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
    target: _Target = Path("."),
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
    target: _Target = Path("."),
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
    target: _Target = Path("."),
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
    target: _Target = Path("."),
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
    target: _Target = Path("."),
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
        await _build(root, report)
    report("preparing UI…")
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        return render_app(await build_payload(index))


@graph_app.command("serve")
def graph_serve(
    target: _Target = Path("."),
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
    target: _Target = Path("."),
    fmt: Annotated[str, typer.Option("--format")] = "dot",
    cluster: str | None = None,
    symbol: str | None = None,
    depth: int = 1,
) -> None:
    """Export a Graphviz DOT (or SVG via the system graphviz) of the graph/cluster/ego."""
    root = find_root(target)

    async def do_export() -> str:
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            payload = await build_payload(index)
        return to_dot(payload, cluster=cluster, symbol=symbol, depth=depth)

    dot = run(do_export(), "exporting…")
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
