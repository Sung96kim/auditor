"""``GRAPH_OVERRIDE`` has exactly one definition, in the stdlib-only graph package __init__."""

import re
from pathlib import Path

from auditor.cli import graph as cli_graph
from auditor.graph import GRAPH_OVERRIDE, scan
from auditor.mcp import graph_tools

_PACKAGE = Path(__file__).resolve().parents[2] / "auditor"
_ASSIGNMENT = re.compile(r"^GRAPH_OVERRIDE\s*[:=]", re.MULTILINE)


def test_forces_graph_extraction_on():
    assert GRAPH_OVERRIDE == {"graph": {"enabled": True}}


def test_every_call_site_shares_the_one_scan():
    """One body, three callers: the CLI, the MCP tool and the observer's session-start build."""
    assert scan.GRAPH_OVERRIDE is GRAPH_OVERRIDE
    assert cli_graph.autoscan is scan.autoscan
    assert graph_tools.autoscan is scan.autoscan


def test_assigned_in_exactly_one_module():
    modules = sorted(
        path.relative_to(_PACKAGE).as_posix()
        for path in _PACKAGE.rglob("*.py")
        if _ASSIGNMENT.search(path.read_text())
    )
    assert modules == ["graph/__init__.py"]
