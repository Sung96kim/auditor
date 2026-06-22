"""mcp_server.py: the FastMCP server registers the expected tools and they call the core."""

import json

import pytest
from fastmcp.exceptions import ToolError

from auditor.mcp_server import _GRAPH_OK, mcp


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
        "finding_detail",
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


async def test_scan_tool_rule_filter(sample_repo):
    src = str(sample_repo / "src")
    data = _structured(
        await mcp.call_tool("scan", {"path": src, "rule": ["PY-SEC-DANGEROUS-EVAL"]})
    )
    kept = {x["rule_id"] for f in data["files"] for x in f["findings"]}
    assert kept == {"PY-SEC-DANGEROUS-EVAL"}


async def test_scan_tool_unknown_rule_errors(sample_repo):
    with pytest.raises(ToolError, match="unknown rule"):
        await mcp.call_tool(
            "scan", {"path": str(sample_repo / "src"), "rule": ["NOPE-NOPE"]}
        )


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


async def test_rules_list_framework_filter():
    result = await mcp.call_tool("rules_list", {"framework": "pytest"})
    rows = _structured(result)
    assert rows and all(r["framework"] == "pytest" for r in rows)


async def test_scan_tool_config_override(sample_repo):
    data = _structured(
        await mcp.call_tool(
            "scan",
            {
                "path": str(sample_repo / "src"),
                "config": {"rules": {"PY-SEC-DANGEROUS-EVAL": {"severity": "low"}}},
            },
        )
    )
    sev = next(
        x["severity"]
        for f in data["files"]
        for x in f["findings"]
        if x["rule_id"] == "PY-SEC-DANGEROUS-EVAL"
    )
    assert sev == "low"


async def test_scan_tool_bad_config_errors(sample_repo):
    with pytest.raises(ToolError, match="invalid config"):
        await mcp.call_tool(
            "scan", {"path": str(sample_repo / "src"), "config": {"nope": 1}}
        )


# --- new gap-fill tests -------------------------------------------------------------------


async def test_scan_severity_filter(sample_repo):
    """scan with severity=['blocking'] returns only blocking findings."""
    data = _structured(
        await mcp.call_tool(
            "scan",
            {"path": str(sample_repo / "src"), "severity": ["blocking"]},
        )
    )
    findings = [f for fl in data["files"] for f in fl["findings"]]
    assert findings  # at least one blocking finding in the sample repo
    assert all(f["severity"] == "blocking" for f in findings)


async def test_scan_unknown_rule_did_you_mean(sample_repo):
    """scan with a near-miss rule id surfaces a 'Did you mean …?' hint in the ToolError."""
    with pytest.raises(ToolError) as exc:
        await mcp.call_tool(
            "scan",
            {"path": str(sample_repo / "src"), "rule": ["PY-SEC-DANGEROUS-EVL"]},
        )
    assert "Did you mean 'PY-SEC-DANGEROUS-EVAL'" in str(exc.value)


async def test_manifest_syntax_error(tmp_path):
    """manifest on a file with a SyntaxError raises ToolError containing 'could not parse'."""
    bad = tmp_path / "bad.py"
    bad.write_text("def f(\n")
    with pytest.raises(ToolError) as exc:
        await mcp.call_tool("manifest", {"file": str(bad)})
    assert "could not parse" in str(exc.value)


async def test_ignore_add_file_and_line_evidence(sample_repo):
    """ignore_add with file + line stores the line number in the returned dict."""
    src = str(sample_repo / "src")
    scan_data = _structured(await mcp.call_tool("scan", {"path": src}))
    # Find the first finding that has a concrete file + line from integrations.py
    finding = next(
        f
        for fl in scan_data["files"]
        for f in fl["findings"]
        if f["rule_id"] == "PY-SEC-DANGEROUS-EVAL"
    )
    file_rel = next(
        fl["file"] for fl in scan_data["files"] if finding in fl["findings"]
    )
    line = finding["line"]
    # Build the absolute path the ignore_add tool needs
    file_abs = str(sample_repo / "src" / file_rel.split("/")[-1])
    result = _structured(
        await mcp.call_tool(
            "ignore_add",
            {
                "rule_id": "PY-SEC-DANGEROUS-EVAL",
                "file": file_abs,
                "line": line,
                "path": src,
            },
        )
    )
    assert result["line"] == line


async def test_rules_list_standard_bandit():
    """rules_list(standard='bandit') returns only rows that have a 'bandit:' ref."""
    rows = _structured(await mcp.call_tool("rules_list", {"standard": "bandit"}))
    assert rows
    assert all(
        any(ref.startswith("bandit:") for ref in row["standard_refs"]) for row in rows
    )


async def test_scan_default_is_compact(sample_repo):
    data = _structured(await mcp.call_tool("scan", {"path": str(sample_repo / "src")}))
    assert "rules" in data  # compact hoists a rules map
    f = next(f for fl in data["files"] for f in fl["findings"])
    assert "evidence" not in f and set(f) <= {
        "rule_id",
        "severity",
        "line",
        "message",
        "suggestion",
    }


async def test_scan_full_restores_legacy_shape(sample_repo):
    data = _structured(
        await mcp.call_tool(
            "scan", {"path": str(sample_repo / "src"), "detail": "full"}
        )
    )
    assert "rules" not in data
    f = next(f for fl in data["files"] for f in fl["findings"])
    assert "evidence" in f and "category" in f


async def test_scan_summary(sample_repo):
    data = _structured(
        await mcp.call_tool(
            "scan", {"path": str(sample_repo / "src"), "detail": "summary"}
        )
    )
    assert set(data) == {"totals", "by_rule", "by_file"}


async def test_scan_bad_detail_errors(sample_repo):
    with pytest.raises(ToolError, match="detail must be"):
        await mcp.call_tool(
            "scan", {"path": str(sample_repo / "src"), "detail": "tiny"}
        )


async def test_report_bad_detail_errors(sample_repo):
    with pytest.raises(ToolError, match="detail must be"):
        await mcp.call_tool(
            "report",
            {"file": str(sample_repo / "src" / "integrations.py"), "detail": "tiny"},
        )


async def test_finding_detail_recovers_evidence(sample_repo):
    src = str(sample_repo / "src")
    scan = _structured(
        await mcp.call_tool("scan", {"path": src})
    )  # compact, no evidence
    fl, f = next(
        (fl, f)
        for fl in scan["files"]
        for f in fl["findings"]
        if f["rule_id"] == "PY-SEC-DANGEROUS-EVAL"
    )
    file_abs = str(sample_repo / "src" / fl["file"].split("/")[-1])
    detail = _structured(
        await mcp.call_tool(
            "finding_detail",
            {"file": file_abs, "rule_id": f["rule_id"], "line": f["line"]},
        )
    )
    assert detail["rule_id"] == f["rule_id"] and detail["line"] == f["line"]
    assert detail["evidence"]  # the source line compact dropped, recovered
    assert "suggestion" in detail


async def test_finding_detail_missing_raises(sample_repo):
    f = str(sample_repo / "src" / "clean.py")
    with pytest.raises(ToolError, match="no .* finding at"):
        await mcp.call_tool(
            "finding_detail",
            {"file": f, "rule_id": "PY-SEC-DANGEROUS-EVAL", "line": 999},
        )


async def test_scan_since_head(tmp_path):
    """scan with since='HEAD' on a committed git repo succeeds (smoke)."""
    import subprocess

    subprocess.run(
        ["git", "-C", str(tmp_path), "init", "-q", "-b", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "init"],
        check=True,
        capture_output=True,
    )
    data = _structured(
        await mcp.call_tool("scan", {"path": str(tmp_path), "since": "HEAD"})
    )
    assert isinstance(data, dict)
    assert "files" in data and "totals" in data


async def test_finding_detail_reads_from_index(sample_repo):
    """finding_detail returns the index record even when the source file is wiped.

    Proof that it reads from the persisted index and not from a fresh re-scan:
    an incremental scan populates the index, then the source file is overwritten
    so that a re-scan would find nothing — only the index can supply the finding.
    """
    src = str(sample_repo / "src")
    # incremental scan persists findings (incl. evidence) to the isolated index
    scan = _structured(await mcp.call_tool("scan", {"path": src, "incremental": True}))
    fl, f = next(
        (fl, f)
        for fl in scan["files"]
        for f in fl["findings"]
        if f["rule_id"] == "PY-SEC-DANGEROUS-EVAL"
    )
    target = sample_repo / "src" / fl["file"].split("/")[-1]
    # wipe the file so a fresh re-scan would find nothing — only the index can answer now
    target.write_text("x = 1\n")
    detail = _structured(
        await mcp.call_tool(
            "finding_detail",
            {"file": str(target), "rule_id": f["rule_id"], "line": f["line"]},
        )
    )
    assert detail["rule_id"] == f["rule_id"] and detail["line"] == f["line"]
    assert detail["evidence"]  # recovered from the index, not the (now-wiped) file


@pytest.mark.skipif(not _GRAPH_OK, reason="graph extra not installed")
async def test_graph_search_and_usages_tools(sample_repo):
    """graph_search locates symbols and graph_usages returns grouped connectivity with full
    counts — exercised through the real MCP call path."""
    src = str(sample_repo / "src")
    build = _structured(await mcp.call_tool("graph_build", {"path": src}))
    assert build["nodes"] > 0

    found = _structured(
        await mcp.call_tool("graph_search", {"term": "Settings", "path": src})
    )
    assert isinstance(found, list) and found
    assert all("id" in f and "rank" in f for f in found)

    short = found[0]["id"].split("::")[-1].split(".")[-1]
    u = _structured(await mcp.call_tool("graph_usages", {"symbol": short, "path": src}))
    assert {
        "resolved",
        "used_by",
        "depends_on",
        "total_in",
        "total_out",
        "ambiguous",
    } <= set(u)
    assert u["total_in"] == sum(v["count"] for v in u["used_by"].values())

    empty = _structured(
        await mcp.call_tool("graph_usages", {"symbol": "zzz_nope_xyz", "path": src})
    )
    assert empty == {}
