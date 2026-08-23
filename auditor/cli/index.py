"""``auditor index add|list`` — manage the registered audit scope + per-file counts."""

from pathlib import Path

import typer

from auditor.cli.helpers import fail, open_index, open_shared_index, present, run
from auditor.cli.options import RootArg, ScopePaths
from auditor.cli.render import (
    render_index_add,
    render_index_forget,
    render_index_list,
    render_index_repos,
)
from auditor.discovery import find_root
from auditor.paths import repo_key

index_app = typer.Typer(
    no_args_is_help=True, help="Manage the audit-scope index + cache."
)


@index_app.command("add")
def index_add(
    paths: ScopePaths,
    target: RootArg = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Register files as the audit scope."""
    root = find_root(target)
    rels = [
        str(p.relative_to(root)) if p.is_relative_to(root) else str(p) for p in paths
    ]
    run(_index_add(root, rels), "registering scope…")
    present({"added": rels}, render_index_add, as_json=json_)


async def _index_add(root: Path, rels: list[str]) -> None:
    async with await open_index(root) as index:
        await index.files.add_scope(rels)


@index_app.command("list")
def index_list(
    target: RootArg = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List the registered scope + per-file counts."""
    root = find_root(target)
    present(run(_index_list(root), "reading index…"), render_index_list, as_json=json_)


async def _index_list(root: Path) -> list[dict]:
    async with await open_index(root) as index:
        return [e.model_dump(mode="json") for e in await index.files.list()]


@index_app.command("repos")
def index_repos(
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List every repo registered in the shared global index (~/.auditor)."""
    present(run(_index_repos(), "reading index…"), render_index_repos, as_json=json_)


async def _index_repos() -> list[dict]:
    async with await open_shared_index() as index:
        return await index.repos.list()


@index_app.command("forget")
def index_forget(
    target: RootArg = Path("."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Confirm dropping the repo's persistent ignores too."
    ),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Drop this repo's rows from the shared global index (registry row + cascade).

    The cascade takes the repo's persistent ignores with the cached rows, so the command
    refuses without --yes whenever this repo has any.
    """
    root = find_root(target)
    key = repo_key(root)
    ignores = run(_repo_ignores(root), "reading index…")
    if ignores and not yes:
        fail(
            f"{key} has {ignores} persistent ignore(s), which forget deletes along with the "
            f"cached rows; run `auditr ignore list` to review them, then re-run with --yes"
        )
    removed = run(_index_forget(root), "forgetting repo…")
    present(
        {"repo": key, "removed": removed},
        render_index_forget,
        as_json=json_,
    )


async def _repo_ignores(root: Path) -> int:
    async with await open_index(root) as index:
        return len(await index.ignores.list())


async def _index_forget(root: Path) -> bool:
    async with await open_shared_index() as index:
        return await index.repos.forget(repo_key(root))
