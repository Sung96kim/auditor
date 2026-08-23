"""The async SQLite index: one store per concern behind :class:`IndexStore`."""

from pathlib import Path

from auditor.database.base import BaseDB
from auditor.database.store import IndexStore
from auditor.paths import index_db_path, partition_for, repo_key

__all__ = ["BaseDB", "IndexStore", "open_repo_index"]


async def open_repo_index(root: Path) -> IndexStore:
    """Connect to the shared global index, scoped to ``root``'s partition.

    Binds the checkout identity as well as the partition key, so every worktree of one checkout
    shares the refinement tables. The one place that recipe is written; a handle whose identity
    falls back to the repo key cannot see its own refinements.
    """
    return await IndexStore.connect(
        index_db_path(), repo_key(root), partition_for(root)
    )
