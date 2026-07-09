"""Back-compat shim: the MCP server now lives in ``auditor.mcp`` (one module per
tool type). Kept so ``python -m auditor.mcp_server`` and the ``auditr-mcp`` /
``auditor-mcp`` entry points keep resolving."""

from auditor.mcp import main, mcp
from auditor.mcp.graph_tools import (  # noqa: F401 — re-exported for pre-split call sites
    _GRAPH_OK,
)

__all__ = ["mcp", "main"]

if __name__ == "__main__":
    main()
