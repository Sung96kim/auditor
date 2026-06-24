"""Stub ``auditor graph`` sub-app shown when the [graph] extra is not installed."""

from typing import Annotated

import typer
from rich.console import Console

graph_app = typer.Typer(
    no_args_is_help=True,
    help="Build + query the semantic code graph (requires the [graph] extra).",
)

_Target = Annotated[str, typer.Argument(help="Repo root (default: .)")]


def _need_extra() -> None:
    console = Console(stderr=True)
    console.print(
        "[bold]auditor graph[/] requires the optional [bold]\\[graph][/] dependencies "
        "(numpy, scikit-learn, networkx).\n"
        "Install them with:  [cyan]pip install 'auditr\\[graph]'[/]   "
        "(or  [cyan]uv pip install 'auditr\\[graph]'[/])"
    )
    raise typer.Exit(1)


@graph_app.command("build")
def graph_build(target: _Target = ".") -> None:
    """Build the semantic graph (requires the [graph] extra)."""
    _need_extra()


@graph_app.command("serve")
def graph_serve(target: _Target = ".") -> None:
    """Serve the interactive graph UI (requires the [graph] extra)."""
    _need_extra()


@graph_app.command("export")
def graph_export(target: _Target = ".") -> None:
    """Export a Graphviz DOT of the graph (requires the [graph] extra)."""
    _need_extra()


@graph_app.command("related")
def graph_related(symbol: str, target: _Target = ".") -> None:
    """Top semantic neighbors of a symbol (requires the [graph] extra)."""
    _need_extra()


@graph_app.command("neighbors")
def graph_neighbors(symbol: str, target: _Target = ".") -> None:
    """Structural neighbors (requires the [graph] extra)."""
    _need_extra()


@graph_app.command("concept")
def graph_concept(term: str, target: _Target = ".") -> None:
    """Symbols in a concept cluster (requires the [graph] extra)."""
    _need_extra()


@graph_app.command("clusters")
def graph_clusters(target: _Target = ".") -> None:
    """List concept clusters (requires the [graph] extra)."""
    _need_extra()
