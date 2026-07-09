"""Shared private helpers used by more than one tool module."""

from fastmcp.exceptions import ToolError


def _validate_detail(detail: str) -> None:
    if detail not in ("summary", "compact", "full"):
        raise ToolError(
            f"detail must be one of: summary, compact, full (got {detail!r})"
        )
