"""Regression tests for graph build/serve auto-scan (Phase V fix).

Verifies that `graph build` (without --no-scan) forces graph extraction even
when the repo config has no [tool.auditor.graph] section.
"""

import json

import pytest
from typer.testing import CliRunner

from auditor.cli import app
from auditor.database import IndexStore
from auditor.engine import audit_target
from auditor.paths import index_db_path, repo_key

runner = CliRunner()


@pytest.fixture
def no_graph_config_repo(tmp_path, monkeypatch):
    """A repo with NO [tool.auditor.graph] config — graph.enabled defaults to False."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "m.py").write_text(
        "def get_user(uid):\n    return db.fetch(uid)\n\n"
        "def fetch_user(uid):\n    return db.fetch(uid)\n"
    )
    return tmp_path


def test_graph_build_autoscan_produces_nodes(no_graph_config_repo):
    """Regression: graph build on a no-config repo should return nodes > 0 via auto-scan."""
    result = runner.invoke(app, ["graph", "build", str(no_graph_config_repo)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["nodes"] > 0, f"expected nodes > 0 but got {data}"


def test_graph_build_no_scan_skips_extraction(no_graph_config_repo):
    """--no-scan skips the scan step; a fresh index with no prior facts yields nodes == 0."""
    result = runner.invoke(
        app, ["graph", "build", str(no_graph_config_repo), "--no-scan"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["nodes"] == 0, f"expected nodes == 0 with --no-scan but got {data}"


def test_graph_build_rebuild_clears_and_reextracts(no_graph_config_repo):
    """--rebuild discards cached facts then re-extracts, so an extractor change is picked up
    even though file contents (and their hashes) are unchanged."""
    first = runner.invoke(app, ["graph", "build", str(no_graph_config_repo)])
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["nodes"] > 0

    rebuilt = runner.invoke(
        app, ["graph", "build", str(no_graph_config_repo), "--rebuild"]
    )
    assert rebuilt.exit_code == 0, rebuilt.output
    # facts were cleared then re-extracted from source → a non-empty graph again
    assert json.loads(rebuilt.stdout)["nodes"] > 0


async def test_serve_reuses_existing_graph_without_rebuild(
    no_graph_config_repo, monkeypatch
):
    """Relaunching `graph serve` on an already-built graph must NOT re-scan or rebuild — that
    was the slow spin-up. It serves the persisted graph directly."""
    from auditor.cli import graph as gmod

    await audit_target(
        no_graph_config_repo,
        incremental=True,
        config_overrides={"graph": {"enabled": True}},
    )
    await gmod._build(no_graph_config_repo)

    def boom(*_a, **_k):
        raise AssertionError("serve must not rebuild when the graph already exists")

    monkeypatch.setattr(gmod, "_autoscan", boom)
    monkeypatch.setattr(gmod, "_build", boom)
    html = await gmod._serve_html(
        no_graph_config_repo, rebuild=False, report=lambda _m: None
    )
    assert "get_user" in html and "__AUDITOR_GRAPH__" in html


async def test_autoscan_writes_graph_facts(no_graph_config_repo):
    """After auto-scan with forced override, index.graph.all_facts() is non-empty."""
    await audit_target(
        no_graph_config_repo,
        incremental=True,
        config_overrides={"graph": {"enabled": True}},
    )
    async with await IndexStore.connect(
        index_db_path(), repo_key(no_graph_config_repo)
    ) as idx:
        blobs = await idx.graph.all_facts()
    assert blobs, "expected graph facts to be written by forced override"
    assert "get_user" in blobs[0]
