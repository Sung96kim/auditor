"""Tests for `graph export` CLI command."""

from unittest.mock import patch

from typer.testing import CliRunner

from auditor.cli import app

runner = CliRunner()


def _write_repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.graph]\nenabled=true\nname_similarity_threshold=0.2\n"
    )
    (tmp_path / "m.py").write_text(
        "def get_user(uid):\n    return db.fetch(uid)\n\n"
        "def fetch_user(uid):\n    return db.fetch(uid)\n"
    )


def test_graph_export_dot(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    _write_repo(tmp_path)
    assert runner.invoke(app, ["scan", str(tmp_path), "-i"]).exit_code == 0
    assert runner.invoke(app, ["graph", "build", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["graph", "export", str(tmp_path), "--format", "dot"])
    assert result.exit_code == 0, result.stdout
    assert result.stdout.startswith("digraph")
    assert "rankdir=LR" in result.stdout


def test_graph_export_default_is_dot(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    _write_repo(tmp_path)
    assert runner.invoke(app, ["scan", str(tmp_path), "-i"]).exit_code == 0
    assert runner.invoke(app, ["graph", "build", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["graph", "export", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert result.stdout.startswith("digraph")


def test_graph_export_svg_no_graphviz(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    _write_repo(tmp_path)
    assert runner.invoke(app, ["scan", str(tmp_path), "-i"]).exit_code == 0
    assert runner.invoke(app, ["graph", "build", str(tmp_path)]).exit_code == 0
    with patch("auditor.cli.graph.shutil.which", return_value=None):
        result = runner.invoke(
            app, ["graph", "export", str(tmp_path), "--format", "svg"]
        )
    assert result.exit_code != 0
    assert "graphviz" in result.output.lower() or "dot" in result.output


def test_graph_export_invalid_format(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    _write_repo(tmp_path)
    assert runner.invoke(app, ["scan", str(tmp_path), "-i"]).exit_code == 0
    assert runner.invoke(app, ["graph", "build", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["graph", "export", str(tmp_path), "--format", "png"])
    assert result.exit_code != 0
    assert "--format" in result.output or "dot or svg" in result.output
