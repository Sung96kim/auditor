"""Shared private helpers used by more than one tool module."""

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

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
