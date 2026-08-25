"""Shared helpers used by more than one tool module: the behaviour annotations, the config edge,
and the preamble every tool runs before it touches a repo."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, ValidationError

from auditor.config import AuditorSettings, ConfigError, load_config
from auditor.config_notice import format_config_error
from auditor.database import IndexStore, open_repo_index
from auditor.database.base import UnmigratableColumn
from auditor.discovery import find_root
from auditor.paths import index_db_path, repo_dir_for_identity
from auditor.user_settings import UserSettings, load_user_settings

# Behaviour hints surfaced to clients at no token cost: clients skip confirmation prompts for
# read-only tools and can cache idempotent ones. All auditor tools work on the local repo only,
# so none touch an open world.
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
MUTATING = ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, idempotentHint=True, destructiveHint=True, openWorldHint=False
)


def validate_detail(detail: str) -> None:
    if detail not in ("summary", "compact", "full"):
        raise ToolError(
            f"detail must be one of: summary, compact, full (got {detail!r})"
        )


def config_error(exc: ConfigError | ValidationError) -> ToolError:
    """A configuration failure as a tool error, worded the way the CLI words it."""
    return ToolError(f"invalid config: {format_config_error(exc)}")


def tool_config(root: Path) -> AuditorSettings:
    """Load the repo policy for a tool call, with any config failure raised as a tool error.

    The MCP half of ``cli.helpers.load_settings``: no config problem may reach a client as a
    traceback on the server's stderr.
    """
    try:
        return load_config(root)
    except (ConfigError, ValidationError) as exc:
        raise config_error(exc) from exc


class ToolRepo(BaseModel):
    """One tool call's repo: the root it resolved, the policy that root loads, and the index
    handle bound to it.

    The handle carries the checkout identity as well as the partition key, so a tool can read the
    refinement rows a sibling worktree wrote.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    root: Path
    index: IndexStore
    settings: AuditorSettings


async def tool_user(repo: ToolRepo) -> UserSettings:
    """The user's own settings for one tool call, read off the loop and refused as one line.

    `load_user_settings` reads two files and, without the state dir, shells out to git for it: on
    the loop every other tool call on this server waits behind that. The identity the handle
    already resolved is what names the dir, so the git call is skipped rather than threaded.
    """
    try:
        return await asyncio.to_thread(
            load_user_settings,
            repo.root,
            directory=repo_dir_for_identity(repo.index.partition.identity),
        )
    except ValidationError as exc:
        raise config_error(exc) from exc


@asynccontextmanager
async def tool_repo_at(root: Path) -> AsyncIterator[ToolRepo]:
    """Hold an index handle bound to an already-resolved root for the block.

    The policy is loaded first and once: a repo whose configuration does not load is one tool
    error from every tool, before any handle is opened. ``finding_detail`` resolves its root from
    the file it was asked about rather than from a directory argument, and this is what it uses.
    """
    settings = tool_config(root)
    try:
        index = await open_repo_index(root)
    except UnmigratableColumn as exc:
        raise ToolError(
            f"the index cannot be upgraded: {exc}. Delete it and re-scan: "
            f"rm {index_db_path()}"
        ) from exc
    async with index:
        yield ToolRepo(root=root, index=index, settings=settings)


@asynccontextmanager
async def tool_repo(path: str) -> AsyncIterator[ToolRepo]:
    """Resolve ``path`` to a project root and hold an index handle bound to it for the block.

    The one place a tool does either, so no tool can reach the index without the identity that
    scopes the refinement tables.
    """
    async with tool_repo_at(find_root(Path(path))) as repo:
        yield repo
