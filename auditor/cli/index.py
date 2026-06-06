"""``auditor index add|list`` — manage the registered audit scope + per-file counts."""

from pathlib import Path

import typer

from auditor.cli.helpers import _echo_json, _open_index, _open_shared_index, _run
from auditor.cli.options import RootArg, ScopePaths
from auditor.discovery import find_root
from auditor.paths import repo_key

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
    async with await _open_index(root) as index:
        await index.add_scope(rels)


@index_app.command("list")
def index_list(target: RootArg = Path(".")) -> None:
    """List the registered scope + per-file counts."""
    root = find_root(target)
    _echo_json(_run(_index_list(root), "reading index…"))


async def _index_list(root: Path) -> list[dict]:
    async with await _open_index(root) as index:
        return [e.model_dump(mode="json") for e in await index.files()]


@index_app.command("repos")
def index_repos() -> None:
    """List every repo registered in the shared global index (~/.auditor)."""
    _echo_json(_run(_index_repos(), "reading index…"))


async def _index_repos() -> list[dict]:
    async with await _open_shared_index() as index:
        return await index.repos()


@index_app.command("forget")
def index_forget(target: RootArg = Path(".")) -> None:
    """Drop this repo's cached data from the shared global index (registry row + cascade)."""
    root = find_root(target)
    removed = _run(_index_forget(root), "forgetting repo…")
    _echo_json({"repo": repo_key(root), "removed": removed})


async def _index_forget(root: Path) -> bool:
    async with await _open_shared_index() as index:
        return await index.forget(repo_key(root))
