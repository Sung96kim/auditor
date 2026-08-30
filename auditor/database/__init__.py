"""The async SQLite index: one store per concern behind :class:`IndexStore`."""

from pathlib import Path

from auditor.database.base import DEFAULT_REPO, BaseDB
from auditor.database.store import IndexStore
from auditor.paths import index_db_path, partition_for, repo_key

__all__ = ["BaseDB", "IndexStore", "open_repo_index", "open_shared_index"]


async def open_repo_index(root: Path) -> IndexStore:
    """Connect to the shared global index, scoped to ``root``'s partition.

    Binds the checkout identity as well as the partition key, so every worktree of one checkout
    shares the refinement tables. The one place that recipe is written; a handle whose identity
    falls back to the repo key cannot see its own refinements.
    """
    return await IndexStore.connect(
        index_db_path(), repo_key(root), partition_for(root)
    )


async def open_shared_index() -> IndexStore:
    """Connect to the shared global index, bound to no repo's partition.

    Beside :func:`open_repo_index` rather than in the CLI, because the daemon reads it too and must
    not import a module whose error path calls ``typer.Exit``.
    """
    return await IndexStore.connect(index_db_path(), DEFAULT_REPO)
