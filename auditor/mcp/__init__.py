"""MCP server (FastMCP), split one file per tool type. ``server`` holds the ``FastMCP``
instance and ``main()``; ``helpers`` holds cross-module private helpers; each ``*_tools``
module owns its ``@mcp.tool`` handlers. This package ``__init__`` is the composition root:
importing the tool modules registers their tools as a side effect. ``auditor.mcp_server``
re-exports ``mcp``/``main`` from here for the pre-split entry points."""

from auditor.mcp import (  # noqa: F401 — imported for their @mcp.tool()/@mcp.resource() side effects
    artifacts,
    graph_tools,
    ignore_tools,
    malware_tools,
    rules_tools,
    scan_tools,
)
from auditor.mcp.code_mode import enable_code_mode
from auditor.mcp.server import main, mcp

# Opt-in Code Mode pilot (no-op unless the [code-mode] extra is installed AND AUDITOR_CODE_MODE
# is set) — see auditor.mcp.code_mode.
enable_code_mode(mcp)

__all__ = ["mcp", "main"]
