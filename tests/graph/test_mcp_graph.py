from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from auditor.engine import audit_target
from auditor.graph.model import QUEUE_ID_CAP
from auditor.mcp_server import mcp


def _data(result):
    return result.data if hasattr(result, "data") else result


async def test_graph_build_then_related(graph_repo: Path):
    await audit_target(graph_repo, incremental=True)  # populate facts
    async with Client(mcp) as c:
        built = _data(await c.call_tool("graph_build", {"path": str(graph_repo)}))
        assert built["nodes"] >= 2
        rel = _data(
            await c.call_tool(
                "graph_related", {"symbol": "get_user", "path": str(graph_repo)}
            )
        )
        assert any("fetch_user" in r["id"] for r in rel)


async def test_graph_build_autoscans(graph_repo_unconfigured: Path):
    """No graph config + no prior scan → graph_build forces the scan and populates nodes."""
    async with Client(mcp) as c:
        built = _data(
            await c.call_tool("graph_build", {"path": str(graph_repo_unconfigured)})
        )
        assert built["nodes"] > 0


async def test_graph_build_no_scan_on_fresh_index(graph_repo_unconfigured: Path):
    """scan=False on a fresh (un-scanned) index builds from nothing → zero nodes."""
    async with Client(mcp) as c:
        built = _data(
            await c.call_tool(
                "graph_build", {"path": str(graph_repo_unconfigured), "scan": False}
            )
        )
        assert built["nodes"] == 0


async def test_graph_concept_is_capped(graph_repo: Path):
    await audit_target(graph_repo, incremental=True)
    async with Client(mcp) as c:
        await c.call_tool("graph_build", {"path": str(graph_repo)})
        clusters = _data(await c.call_tool("graph_clusters", {"path": str(graph_repo)}))
        assert clusters, "expected at least one cluster"
        biggest = max(clusters, key=lambda c: c["member_count"])
        concept = _data(
            await c.call_tool(
                "graph_concept",
                {"term": biggest["label"], "path": str(graph_repo), "limit": 1},
            )
        )
        assert concept["member_count"] >= 1
        assert len(concept["members"]) <= 1
        assert concept["shown"] == len(concept["members"])
        assert concept["member_count"] == biggest["member_count"]


async def test_graph_neighbors_is_capped(graph_repo_with_calls: Path):
    """``limit`` is the tool's only logic, so give it a symbol with more hops than the cap."""
    path = str(graph_repo_with_calls)
    await audit_target(graph_repo_with_calls, incremental=True)
    async with Client(mcp) as c:
        await c.call_tool("graph_build", {"path": path})
        every = _data(
            await c.call_tool("graph_neighbors", {"symbol": "load_user", "path": path})
        )
        assert len(every) > 1, every
        capped = _data(
            await c.call_tool(
                "graph_neighbors", {"symbol": "load_user", "path": path, "limit": 1}
            )
        )
        assert len(capped) == 1


async def test_graph_overview_shape(graph_repo: Path):
    await audit_target(graph_repo, incremental=True)
    async with Client(mcp) as c:
        await c.call_tool("graph_build", {"path": str(graph_repo)})
        ov = _data(await c.call_tool("graph_overview", {"path": str(graph_repo)}))
        assert isinstance(ov["nodes"], int) and ov["nodes"] > 0
        assert isinstance(ov["edges"], int)
        assert isinstance(ov["clusters"], int)
        assert isinstance(ov["top_clusters"], list) and len(ov["top_clusters"]) <= 8
        assert all({"label", "size"} <= set(c) for c in ov["top_clusters"])
        assert isinstance(ov["god_concepts"], list) and len(ov["god_concepts"]) <= 5
        assert isinstance(ov["bottlenecks"], list) and len(ov["bottlenecks"]) <= 5
        assert ov["god_concept_count"] >= len(ov["god_concepts"])
        assert ov["bottleneck_count"] >= len(ov["bottlenecks"])


async def test_graph_unresolved_lists_the_queue(graph_repo: Path):
    (graph_repo / "helper.py").write_text("def handle():\n    return 1\n")
    (graph_repo / "caller.py").write_text("def use():\n    return handle()\n")
    (graph_repo / "attr_caller.py").write_text(
        "def go(job):\n    return job.handle()\n"
    )
    await audit_target(graph_repo, incremental=True)
    async with Client(mcp) as c:
        await c.call_tool("graph_build", {"path": str(graph_repo)})
        rows = _data(await c.call_tool("graph_unresolved", {"path": str(graph_repo)}))
        by_key = {(r["node_id"], r["name"]): r for r in rows}
        row = by_key["caller.py::use", "handle"]
        assert row["definers"] == ["helper.py::handle"]
        assert row["definers_count"] == 1  # capped list, true total alongside
        assert row["candidates"] == [] and row["candidates_count"] == 0
        only_sparse = _data(
            await c.call_tool(
                "graph_unresolved", {"path": str(graph_repo), "reason": ["text_sparse"]}
            )
        )
        assert only_sparse and all(r["reason"] == "text_sparse" for r in only_sparse)
        attr = _data(
            await c.call_tool(
                "graph_unresolved", {"path": str(graph_repo), "call_form": ["attr"]}
            )
        )
        assert all(r["call_form"] == "attr" for r in attr)
        assert ("attr_caller.py::go", "handle") in {
            (r["node_id"], r["name"]) for r in attr
        }
        both = _data(
            await c.call_tool(
                "graph_unresolved",
                {
                    "path": str(graph_repo),
                    "reason": ["ambiguous_name", "unimportable_name"],
                },
            )
        )
        assert both and all(
            r["reason"] in ("ambiguous_name", "unimportable_name") for r in both
        )
        capped = _data(
            await c.call_tool("graph_unresolved", {"path": str(graph_repo), "limit": 1})
        )
        assert len(capped) == 1
        assert capped[0] == rows[0]


@pytest.mark.parametrize(
    ("field", "value"), [("reason", "ambigous_name"), ("call_form", "barre")]
)
async def test_graph_unresolved_rejects_an_unknown_filter_value(
    graph_repo: Path, field: str, value: str
):
    """A typo must be a tool error, not an empty page the agent reads as an empty queue."""
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match=value):
            await c.call_tool(
                "graph_unresolved", {"path": str(graph_repo), field: [value]}
            )


async def test_graph_unresolved_caps_the_id_lists_at_the_shared_cap(graph_repo: Path):
    """The cap the docstring promises: more definers than the cap, list truncated, true total
    reported. The fixture size is a literal, so raising the cap fails here instead of tracking it."""
    definers = 12
    assert definers > QUEUE_ID_CAP, "the fixture must define more than the cap"
    for i in range(definers):
        (graph_repo / f"d{i}.py").write_text("def handle():\n    return 1\n")
    (graph_repo / "caller.py").write_text("def use():\n    return handle()\n")
    await audit_target(graph_repo, incremental=True)
    async with Client(mcp) as c:
        await c.call_tool("graph_build", {"path": str(graph_repo)})
        rows = _data(await c.call_tool("graph_unresolved", {"path": str(graph_repo)}))
    row = next(
        r for r in rows if (r["node_id"], r["name"]) == ("caller.py::use", "handle")
    )
    assert len(row["definers"]) == QUEUE_ID_CAP
    assert row["definers_count"] == definers


async def test_graph_unresolved_can_drop_the_externally_bound_rows(graph_repo: Path):
    (graph_repo / "helper.py").write_text("def handle():\n    return 1\n")
    (graph_repo / "ext_caller.py").write_text(
        "import re\ndef find(s):\n    return re.handle(s)\n"
    )
    await audit_target(graph_repo, incremental=True)
    async with Client(mcp) as c:
        await c.call_tool("graph_build", {"path": str(graph_repo)})
        shown = _data(await c.call_tool("graph_unresolved", {"path": str(graph_repo)}))
        hidden = _data(
            await c.call_tool(
                "graph_unresolved", {"path": str(graph_repo), "external": False}
            )
        )
    assert any(r["externally_bound"] for r in shown)
    assert hidden and not any(r["externally_bound"] for r in hidden)


async def test_graph_unresolved_before_a_build_is_empty(graph_repo: Path):
    async with Client(mcp) as c:
        assert (
            _data(await c.call_tool("graph_unresolved", {"path": str(graph_repo)}))
            == []
        )
