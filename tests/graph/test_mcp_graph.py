import pytest

from auditor.engine import audit_target


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


async def test_graph_build_then_related(repo):
    from fastmcp import Client

    from auditor.mcp_server import mcp

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
