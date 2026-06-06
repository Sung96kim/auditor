"""mcp_server.py: the FastMCP server registers the expected tools and they call the core."""

import json

import pytest
from fastmcp.exceptions import ToolError

from auditor.mcp_server import mcp


@pytest.mark.parametrize(
    "tool, args, message",
    [
        ("report", {"file": "/no/such.py"}, "no such file"),
        ("manifest", {"file": "/no/such.py"}, "no such file"),
        ("manifest", {"file": "pyproject.toml"}, "Python-only"),  # exists, wrong type
        ("scan", {"path": "/no/such/dir"}, "no such path"),
    ],
)
async def test_missing_inputs_raise_clean_tool_errors(tool, args, message):
    """Regression: missing/invalid inputs surface a clean ToolError, not a raw OSError traceback."""
    with pytest.raises(ToolError) as exc:
        await mcp.call_tool(tool, args)
    assert message in str(exc.value)


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
        "ignore_add",
        "ignore_list",
        "ignore_remove",
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


async def test_ignore_tools_roundtrip(sample_repo):
    """ignore_add hides a rule on the next scan; ignore_list shows it; ignore_remove restores."""
    src = str(sample_repo / "src")
    rules_before = {
        x["rule_id"]
        for f in _structured(await mcp.call_tool("scan", {"path": src}))["files"]
        for x in f["findings"]
    }
    assert "PY-TYPING-MISSING-HINTS" in rules_before

    added = _structured(
        await mcp.call_tool(
            "ignore_add", {"rule_id": "PY-TYPING-MISSING-HINTS", "path": src}
        )
    )
    assert added["id"] >= 1

    listed = _structured(await mcp.call_tool("ignore_list", {"path": src}))
    assert [r["rule_id"] for r in listed] == ["PY-TYPING-MISSING-HINTS"]

    scanned = _structured(await mcp.call_tool("scan", {"path": src}))
    rules_after = {x["rule_id"] for f in scanned["files"] for x in f["findings"]}
    assert "PY-TYPING-MISSING-HINTS" not in rules_after
    assert scanned["totals"]["ignored"] >= 1

    # show_ignored reveals
    shown = _structured(
        await mcp.call_tool("scan", {"path": src, "show_ignored": True})
    )
    assert "PY-TYPING-MISSING-HINTS" in {
        x["rule_id"] for f in shown["files"] for x in f["findings"]
    }

    removed = _structured(
        await mcp.call_tool("ignore_remove", {"id": added["id"], "path": src})
    )
    assert removed["removed"] is True
    assert _structured(await mcp.call_tool("ignore_list", {"path": src})) == []


async def test_ignore_add_validates_rule_id(sample_repo):
    src = str(sample_repo / "src")
    with pytest.raises(ToolError) as exc:
        await mcp.call_tool("ignore_add", {"rule_id": "PY-NOPE-RULE", "path": src})
    assert "unknown rule_id" in str(exc.value)
    # force escapes the check (e.g. a not-yet-loaded plugin rule)
    out = _structured(
        await mcp.call_tool(
            "ignore_add", {"rule_id": "ACME-PLUGIN-RULE", "path": src, "force": True}
        )
    )
    assert out["rule_id"] == "ACME-PLUGIN-RULE"


async def test_aggregate_tool_reads_shared_index(sample_repo):
    """The aggregate tool reads the shared global index that an incremental scan populated —
    exercises mcp_server's index_db_path()/repo_key() path end-to-end."""
    from auditor.engine import audit_target

    await audit_target(sample_repo / "src", incremental=True, root=sample_repo)
    result = await mcp.call_tool("aggregate", {"path": str(sample_repo / "src")})
    markdown = _structured(result)
    assert isinstance(markdown, str) and "consolidated report" in markdown


async def test_manifest_tool(sample_repo):
    result = await mcp.call_tool(
        "manifest", {"file": str(sample_repo / "src" / "models.py")}
    )
    data = _structured(result)
    assert any(e["symbol"] == "OpportunityRecord" for e in data)
