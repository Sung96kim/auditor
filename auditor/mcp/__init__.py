"""MCP server (FastMCP), split one file per tool type. ``server`` holds the ``FastMCP``
instance and ``main()``; ``helpers`` holds cross-module private helpers; each ``*_tools``
module owns its ``@mcp.tool`` handlers. This package ``__init__`` is the composition root:
importing the tool modules registers their tools as a side effect. ``auditor.mcp_server``
re-exports ``mcp``/``main`` from here for the pre-split entry points."""

from auditor.mcp import (  # noqa: F401 — imported for their @mcp.tool() side effects
    graph_tools,
    ignore_tools,
    malware_tools,
    rules_tools,
    scan_tools,
)
from auditor.mcp.server import main, mcp

__all__ = ["mcp", "main"]
