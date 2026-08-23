"""Tests for `graph export` CLI command."""

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from auditor.cli import app

runner = CliRunner()


def _built(repo: Path) -> None:
    assert runner.invoke(app, ["scan", str(repo), "-i"]).exit_code == 0
    assert runner.invoke(app, ["graph", "build", str(repo)]).exit_code == 0


def test_graph_export_dot(graph_repo: Path):
    _built(graph_repo)
    result = runner.invoke(app, ["graph", "export", str(graph_repo), "--format", "dot"])
    assert result.exit_code == 0, result.stdout
    assert result.stdout.startswith("digraph")
    assert "rankdir=LR" in result.stdout


def test_graph_export_default_is_dot(graph_repo: Path):
    _built(graph_repo)
    result = runner.invoke(app, ["graph", "export", str(graph_repo)])
    assert result.exit_code == 0, result.stdout
    assert result.stdout.startswith("digraph")


def test_graph_export_svg_no_graphviz(graph_repo: Path):
    _built(graph_repo)
    with patch("auditor.cli.graph.shutil.which", return_value=None):
        result = runner.invoke(
            app, ["graph", "export", str(graph_repo), "--format", "svg"]
        )
    assert result.exit_code != 0
    assert "graphviz" in result.output.lower() or "dot" in result.output


def test_graph_export_invalid_format(graph_repo: Path):
    _built(graph_repo)
    result = runner.invoke(app, ["graph", "export", str(graph_repo), "--format", "png"])
    assert result.exit_code != 0
    assert "--format" in result.output or "dot or svg" in result.output


def test_graph_export_flow(graph_repo_flow: Path):
    _built(graph_repo_flow)
    result = runner.invoke(
        app, ["graph", "export", str(graph_repo_flow), "--flow", "entry"]
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout.startswith("digraph flow")
    assert "rank=same" in result.stdout
    assert '"m.py::entry" -> "m.py::middle"' in result.stdout


def test_graph_export_flow_in_reverses_direction(graph_repo_flow: Path):
    _built(graph_repo_flow)
    result = runner.invoke(
        app, ["graph", "export", str(graph_repo_flow), "--flow", "leaf", "--in"]
    )
    assert result.exit_code == 0, result.stdout
    assert '"m.py::leaf" -> "m.py::middle"' in result.stdout


def test_graph_export_flow_rejects_symbol_and_cluster(graph_repo_flow: Path):
    """The modes pick different node sets; combining them would silently prefer one."""
    for extra in (["--symbol", "leaf"], ["--cluster", "x"]):
        result = runner.invoke(
            app, ["graph", "export", str(graph_repo_flow), "--flow", "entry", *extra]
        )
        assert result.exit_code != 0


def test_graph_export_ego_depth_default_is_unchanged(graph_repo_flow: Path):
    """--depth is shared with --flow now, so the ego default of 1 needs a guard."""
    _built(graph_repo_flow)
    result = runner.invoke(
        app, ["graph", "export", str(graph_repo_flow), "--symbol", "entry"]
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout.startswith("digraph codebase")
    assert "m.py::leaf" not in result.stdout
