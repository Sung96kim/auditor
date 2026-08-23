"""Back-compat shim: the MCP server now lives in ``auditor.mcp`` (one module per
tool type). Kept so ``python -m auditor.mcp_server`` and the ``auditr-mcp`` /
``auditor-mcp`` entry points keep resolving."""

from auditor.mcp import main, mcp

__all__ = ["mcp", "main"]

if __name__ == "__main__":
    main()
