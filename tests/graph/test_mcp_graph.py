from pathlib import Path

import pytest
from fastmcp import Client

from auditor.engine import audit_target
from auditor.mcp_server import mcp

_GRAPH_CONFIG = "[tool.auditor.graph]\nenabled=true\nname_similarity_threshold=0.2\n"
_SIMILAR_NAMES = (
    "def get_user(uid):\n    return db.fetch(uid)\n\n"
    "def fetch_user(uid):\n    return db.fetch(uid)\n"
)
_RESOLVABLE_CALLS = (
    "def get_user(uid):\n    return uid\n\n"
    "def fetch_user(uid):\n    return uid\n\n"
    "def load_user(uid):\n    return get_user(uid) or fetch_user(uid)\n"
)


def _write_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_source: str,
    graph_config: str = "",
) -> Path:
    """A one-module repo with its own AUDITOR_HOME, so no index is shared between tests."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n' + graph_config
    )
    (tmp_path / "m.py").write_text(module_source)
    return tmp_path


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _write_repo(tmp_path, monkeypatch, _SIMILAR_NAMES, _GRAPH_CONFIG)


@pytest.fixture
def repo_no_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo with NO [tool.auditor.graph] config — graph_build must still auto-scan."""
    return _write_repo(tmp_path, monkeypatch, _SIMILAR_NAMES)


@pytest.fixture
def repo_with_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo whose calls resolve within the module, so structural neighbors exist to cap."""
    return _write_repo(tmp_path, monkeypatch, _RESOLVABLE_CALLS, _GRAPH_CONFIG)


def _data(result):
    return result.data if hasattr(result, "data") else result


async def test_graph_build_then_related(repo):
    await audit_target(repo, incremental=True)  # populate facts
    async with Client(mcp) as c:
        built = _data(await c.call_tool("graph_build", {"path": str(repo)}))
        assert built["nodes"] >= 2
        rel = _data(
            await c.call_tool(
                "graph_related", {"symbol": "get_user", "path": str(repo)}
            )
        )
        assert any("fetch_user" in r["id"] for r in rel)


async def test_graph_build_autoscans(repo_no_graph):
    """No graph config + no prior scan → graph_build forces the scan and populates nodes."""
    async with Client(mcp) as c:
        built = _data(await c.call_tool("graph_build", {"path": str(repo_no_graph)}))
        assert built["nodes"] > 0


async def test_graph_build_no_scan_on_fresh_index(repo_no_graph):
    """scan=False on a fresh (un-scanned) index builds from nothing → zero nodes."""
    async with Client(mcp) as c:
        built = _data(
            await c.call_tool(
                "graph_build", {"path": str(repo_no_graph), "scan": False}
            )
        )
        assert built["nodes"] == 0


async def test_graph_concept_is_capped(repo):
    await audit_target(repo, incremental=True)
    async with Client(mcp) as c:
        await c.call_tool("graph_build", {"path": str(repo)})
        clusters = _data(await c.call_tool("graph_clusters", {"path": str(repo)}))
        assert clusters, "expected at least one cluster"
        biggest = max(clusters, key=lambda c: c["member_count"])
        concept = _data(
            await c.call_tool(
                "graph_concept",
                {"term": biggest["label"], "path": str(repo), "limit": 1},
            )
        )
        assert concept["member_count"] >= 1
        assert len(concept["members"]) <= 1
        assert concept["shown"] == len(concept["members"])
        assert concept["member_count"] == biggest["member_count"]


async def test_graph_neighbors_is_capped(repo_with_calls):
    """``limit`` is the tool's only logic, so give it a symbol with more hops than the cap."""
    path = str(repo_with_calls)
    await audit_target(repo_with_calls, incremental=True)
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


async def test_graph_overview_shape(repo):
    await audit_target(repo, incremental=True)
    async with Client(mcp) as c:
        await c.call_tool("graph_build", {"path": str(repo)})
        ov = _data(await c.call_tool("graph_overview", {"path": str(repo)}))
        assert isinstance(ov["nodes"], int) and ov["nodes"] > 0
        assert isinstance(ov["edges"], int)
        assert isinstance(ov["clusters"], int)
        assert isinstance(ov["top_clusters"], list) and len(ov["top_clusters"]) <= 8
        assert all({"label", "size"} <= set(c) for c in ov["top_clusters"])
        assert isinstance(ov["god_concepts"], list) and len(ov["god_concepts"]) <= 5
        assert isinstance(ov["bottlenecks"], list) and len(ov["bottlenecks"]) <= 5
        assert ov["god_concept_count"] >= len(ov["god_concepts"])
        assert ov["bottleneck_count"] >= len(ov["bottlenecks"])
