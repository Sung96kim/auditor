import pytest
from fastmcp import Client

from auditor.engine import audit_target
from auditor.mcp_server import mcp


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.graph]\nenabled=true\nname_similarity_threshold=0.2\n"
    )
    (tmp_path / "m.py").write_text(
        "def get_user(uid):\n    return db.fetch(uid)\n\n"
        "def fetch_user(uid):\n    return db.fetch(uid)\n"
    )
    return tmp_path


def _data(result):
    return result.data if hasattr(result, "data") else result


@pytest.fixture
def repo_no_graph(tmp_path, monkeypatch):
    """A repo with NO [tool.auditor.graph] config — graph_build must still auto-scan."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "m.py").write_text(
        "def get_user(uid):\n    return db.fetch(uid)\n\n"
        "def fetch_user(uid):\n    return db.fetch(uid)\n"
    )
    return tmp_path


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
