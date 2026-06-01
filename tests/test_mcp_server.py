"""mcp_server.py: the FastMCP server registers the expected tools and they call the core."""

import json

from auditor.mcp_server import mcp


async def test_tools_registered():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert {
        "scan",
        "report",
        "manifest",
        "discover",
        "aggregate",
        "rules_list",
    } <= names


async def test_rules_list_tool():
    result = await mcp.call_tool("rules_list", {"category": "security"})
    data = _structured(result)
    assert data and all(r["category"] == "security" for r in data)


async def test_report_tool(sample_repo):
    result = await mcp.call_tool(
        "report", {"file": str(sample_repo / "src" / "integrations.py")}
    )
    data = _structured(result)
    rules = {x["rule_id"] for f in data["files"] for x in f["findings"]}
    assert "PY-SEC-DANGEROUS-EVAL" in rules


def _structured(result):
    """FastMCP returns a ToolResult; pull out the structured/JSON payload."""
    if getattr(result, "structured_content", None) is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    if getattr(result, "data", None) is not None:
        return result.data
    return json.loads(result.content[0].text)


async def test_scan_tool(sample_repo):
    result = await mcp.call_tool("scan", {"path": str(sample_repo / "src")})
    data = _structured(result)
    assert data["totals"]["blocking"] >= 1


async def test_discover_tool(sample_repo):
    result = await mcp.call_tool("discover", {"path": str(sample_repo)})
    data = _structured(result)
    assert any(f["role"] == "test" for f in data)


async def test_manifest_tool(sample_repo):
    result = await mcp.call_tool(
        "manifest", {"file": str(sample_repo / "src" / "models.py")}
    )
    data = _structured(result)
    assert any(e["symbol"] == "OpportunityRecord" for e in data)
