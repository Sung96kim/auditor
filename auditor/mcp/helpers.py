"""Shared private helpers used by more than one tool module."""

from pathlib import Path

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import ValidationError

from auditor.config import AuditorSettings, ConfigError, load_config
from auditor.database import IndexStore, open_repo_index
from auditor.database.base import UnmigratableColumn
from auditor.paths import index_db_path

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


async def open_index(root: Path) -> IndexStore:
    """``open_repo_index`` with an unmigratable schema surfaced as a tool error rather than a
    traceback, which is the MCP half of what ``cli.helpers.open_index`` does."""
    try:
        return await open_repo_index(root)
    except UnmigratableColumn as exc:
        raise ToolError(
            f"the index cannot be upgraded: {exc}. Delete it and re-scan: "
            f"rm {index_db_path()}"
        ) from exc


def config_error(exc: ConfigError | ValidationError) -> ToolError:
    """A one-line tool error for a repo config that cannot be used.

    A bad profile, a cycle and unparseable TOML already read as one sentence; a validation error
    is reduced to its first failing field.
    """
    if isinstance(exc, ConfigError):
        return ToolError(f"invalid config: {exc}")
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"])
    return ToolError(f"invalid config: {loc + ': ' if loc else ''}{err['msg']}")


def tool_config(root: Path) -> AuditorSettings:
    """Load the repo policy for a tool call, with any config failure raised as a tool error.

    The MCP half of ``cli.helpers.load_settings``: no config problem may reach a client as a
    traceback on the server's stderr.
    """
    try:
        return load_config(root)
    except (ConfigError, ValidationError) as exc:
        raise config_error(exc) from exc
