"""The incremental scan every graph build runs first, with extraction forced on."""

from pathlib import Path

from auditor.engine import audit_target
from auditor.graph import GRAPH_OVERRIDE


async def autoscan(root: Path) -> None:
    """Incremental scan with graph extraction forced on, whatever ``graph.enabled`` says (D2).

    One body for the three callers that need it: `auditr graph build`, the MCP build tool and the
    observer's session-start work item, which must not import `auditor.cli.graph` to get it.
    """
    await audit_target(root, incremental=True, config_overrides=GRAPH_OVERRIDE)
