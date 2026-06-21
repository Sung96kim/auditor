"""Tests for the graph stub shown when the [graph] extra is absent."""

from typer.testing import CliRunner

from auditor.cli.graph_stub import graph_app

runner = CliRunner()


def test_graph_build_shows_install_hint():
    result = runner.invoke(graph_app, ["build"])
    assert result.exit_code == 1
    assert "pip install 'auditr[graph]'" in result.output


def test_graph_serve_shows_install_hint():
    result = runner.invoke(graph_app, ["serve"])
    assert result.exit_code == 1
    assert "pip install 'auditr[graph]'" in result.output


def test_graph_export_shows_install_hint():
    result = runner.invoke(graph_app, ["export"])
    assert result.exit_code == 1
    assert "pip install 'auditr[graph]'" in result.output


def test_graph_related_shows_install_hint():
    result = runner.invoke(graph_app, ["related", "some_symbol"])
    assert result.exit_code == 1
    assert "pip install 'auditr[graph]'" in result.output


def test_graph_neighbors_shows_install_hint():
    result = runner.invoke(graph_app, ["neighbors", "some_symbol"])
    assert result.exit_code == 1
    assert "pip install 'auditr[graph]'" in result.output


def test_graph_concept_shows_install_hint():
    result = runner.invoke(graph_app, ["concept", "some_term"])
    assert result.exit_code == 1
    assert "pip install 'auditr[graph]'" in result.output


def test_graph_clusters_shows_install_hint():
    result = runner.invoke(graph_app, ["clusters"])
    assert result.exit_code == 1
    assert "pip install 'auditr[graph]'" in result.output
