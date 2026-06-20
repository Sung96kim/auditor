"""``auditor graph build`` — semantic-graph commands (query commands added in Task 12).

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
