import json

import pytest

from auditor.database import IndexStore
from auditor.engine import audit_target
from auditor.paths import index_db_path, repo_key


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor.graph]\nenabled=true\n'
    )
    (tmp_path / "m.py").write_text("def get_user(uid):\n    return db.fetch(uid)\n")
    return tmp_path


async def test_scan_writes_graph_facts_when_enabled(repo):
    await audit_target(repo, incremental=True)
    async with await IndexStore.connect(index_db_path(), repo_key(repo)) as idx:
        blobs = await idx.graph.all_facts()
    assert blobs and "get_user" in blobs[0]
    symbols = [n for n in json.loads(blobs[0])["nodes"] if n["kind"] != "module"]
    assert symbols[0]["name"] == "get_user"


async def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    await audit_target(tmp_path, incremental=True)
    async with await IndexStore.connect(index_db_path(), repo_key(tmp_path)) as idx:
        assert await idx.graph.all_facts() == []


async def test_facts_extracted_on_cache_hit_when_graph_enabled(tmp_path, monkeypatch):
    """Regression: a repo scanned WITHOUT graph is a findings cache-hit on the next
    incremental scan. When that re-scan enables graph (as `graph build`'s auto-scan
    does), facts must still be extracted — otherwise the graph builds empty."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "m.py").write_text("def get_user(uid):\n    return db.fetch(uid)\n")

    # First scan: graph disabled → findings cached, no facts.
    await audit_target(tmp_path, incremental=True)
    async with await IndexStore.connect(index_db_path(), repo_key(tmp_path)) as idx:
        assert await idx.graph.all_facts() == []

    # Re-scan unchanged files with graph forced on (findings are a cache-hit).
    await audit_target(
        tmp_path, incremental=True, config_overrides={"graph": {"enabled": True}}
    )
    async with await IndexStore.connect(index_db_path(), repo_key(tmp_path)) as idx:
        blobs = await idx.graph.all_facts()
    assert blobs and "get_user" in blobs[0]
