"""Shared private helpers used by more than one tool module."""

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import ValidationError

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


def config_error(exc: ValidationError) -> ToolError:
    """A one-line tool error for a repo config that fails validation."""
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"])
    return ToolError(f"invalid config: {loc + ': ' if loc else ''}{err['msg']}")
