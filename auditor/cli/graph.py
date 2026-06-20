"""``auditor graph`` — semantic-graph commands: build|related|neighbors|concept|clusters.

Imported only via a guarded mount in cli/__init__, so the core CLI works without the [graph] extra.
"""

import time
from pathlib import Path

import typer

from auditor.cli.helpers import _echo_json, _run
from auditor.config import load_config
from auditor.database import IndexStore
from auditor.discovery import find_root
from auditor.graph.build import GraphBuilder
from auditor.graph.query import GraphQuery
from auditor.paths import index_db_path, repo_key

graph_app = typer.Typer(
    no_args_is_help=True, help="Build + query the semantic code graph."
)


async def _build(root: Path) -> dict:
    settings = load_config(root)
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        await index.repos.register(time.time())
        return await GraphBuilder().run(index, settings)


@graph_app.command("build")
def graph_build(target: Path = Path(".")) -> None:
    """Build the semantic graph from cached facts (run `scan -i` with graph enabled first)."""
    root = find_root(target)
    _echo_json(_run(_build(root), "building graph…"))


def _query_cmd(fn_name: str):
    async def runner(root: Path, **kw):
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            return await getattr(GraphQuery(index), fn_name)(**kw)

    return runner


@graph_app.command("related")
def graph_related(symbol: str, target: Path = Path("."), limit: int = 10) -> None:
    """Top semantic neighbors of a symbol (name + usage), ranked."""
    root = find_root(target)
    _echo_json(
        _run(_query_cmd("related")(root, symbol=symbol, limit=limit), "querying…")
    )


@graph_app.command("neighbors")
def graph_neighbors(symbol: str, target: Path = Path("."), depth: int = 1) -> None:
    """Structural neighbors (calls/overrides/...) up to a depth."""
    root = find_root(target)
    _echo_json(
        _run(_query_cmd("neighbors")(root, symbol=symbol, depth=depth), "querying…")
    )


@graph_app.command("concept")
def graph_concept(term: str, target: Path = Path(".")) -> None:
    """Symbols in the concept cluster matching a term."""
    root = find_root(target)
    _echo_json(_run(_query_cmd("concept")(root, term=term), "querying…"))


@graph_app.command("clusters")
def graph_clusters(target: Path = Path(".")) -> None:
    """List concept clusters (label + size)."""
    root = find_root(target)
    _echo_json(_run(_query_cmd("clusters")(root), "querying…"))
