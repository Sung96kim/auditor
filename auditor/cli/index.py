"""``auditor index add|list`` — manage the registered audit scope + per-file counts."""

from pathlib import Path

import typer

from auditor.cli.helpers import _echo_json, _index_db, _run
from auditor.cli.options import RootArg, ScopePaths
from auditor.discovery import find_root
from auditor.index import IndexStore

index_app = typer.Typer(
    no_args_is_help=True, help="Manage the audit-scope index + cache."
)


@index_app.command("add")
def index_add(paths: ScopePaths, target: RootArg = Path(".")) -> None:
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
def index_list(target: RootArg = Path(".")) -> None:
    """List the registered scope + per-file counts."""
    root = find_root(target)
    _echo_json(_run(_index_list(root), "reading index…"))


async def _index_list(root: Path) -> list[dict]:
    async with await IndexStore.connect(_index_db(root)) as index:
        return [e.model_dump(mode="json") for e in await index.files()]
