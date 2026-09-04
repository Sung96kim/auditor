# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (MCP tool surface — JSON payloads by contract)
"""rules_list — the detector registry/metadata MCP tool."""

from pathlib import Path

from auditor.discovery import find_root
from auditor.mcp.helpers import READ_ONLY, tool_config
from auditor.mcp.server import mcp
from auditor.registry import REGISTRY


@mcp.tool(annotations=READ_ONLY)
def rules_list(
    root: str = ".",
    category: str | None = None,
    standard: str | None = None,
    framework: str | None = None,
) -> list[dict]:
    """Enumerate detector rules, optionally filtered by category, standard (bandit/owasp), or
    framework (e.g. pytest). Includes the rules ``root``'s trusted plugins contribute; each row
    records the module or file it was registered from."""
    tool_config(find_root(Path(root)))
    rows = REGISTRY.rule_rows(category=category, standard=standard, framework=framework)
    return [row.model_dump() for row in rows]
