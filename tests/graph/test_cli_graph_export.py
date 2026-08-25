"""Tests for `graph export` CLI command."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from auditor.cli import app
from auditor.graph.model import MAX_FLOW_DEPTH

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


def test_graph_export_flow_depth_default_is_four(graph_repo_flow: Path):
    """One --depth, two mode defaults; only the ego one had a guard."""
    _built(graph_repo_flow)
    shallow = runner.invoke(
        app,
        ["graph", "export", str(graph_repo_flow), "--flow", "entry", "--depth", "1"],
    )
    deep = runner.invoke(
        app, ["graph", "export", str(graph_repo_flow), "--flow", "entry"]
    )
    assert shallow.exit_code == 0 and deep.exit_code == 0
    assert "m.py::leaf" not in shallow.stdout and "m.py::leaf" in deep.stdout


def test_graph_export_flow_fails_on_an_unknown_symbol(graph_repo_flow: Path):
    """An empty walk used to render a valid DOT that reported a 200-node cap it never applied."""
    _built(graph_repo_flow)
    result = runner.invoke(
        app, ["graph", "export", str(graph_repo_flow), "--flow", "nope"]
    )
    assert result.exit_code != 0
    assert "no such symbol: nope" in result.output


def test_graph_export_rejects_symbol_with_cluster(graph_repo_flow: Path):
    """to_dot silently prefers --symbol, so the combination answered a question nobody asked."""
    _built(graph_repo_flow)
    result = runner.invoke(
        app,
        [
            "graph",
            "export",
            str(graph_repo_flow),
            "--symbol",
            "entry",
            "--cluster",
            "x",
        ],
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


def test_graph_export_flow_limit_reaches_the_dot_header(graph_repo_flow: Path):
    """The header claims a cap; before this it always claimed 200 whatever the caller asked.

    The limit counts emitted children with the root free, so 1 is what truncates this fixture's
    two-deep chain.
    """
    _built(graph_repo_flow)
    result = runner.invoke(
        app,
        ["graph", "export", str(graph_repo_flow), "--flow", "entry", "--limit", "1"],
    )
    assert result.exit_code == 0, result.stdout
    assert "at most 1 nodes" in result.stdout
    assert "truncated" in result.stdout


def test_graph_export_flow_stop_at_prunes_the_picture(graph_repo_flow: Path):
    """The tree honours --stop-at; the picture used to walk straight past it."""
    _built(graph_repo_flow)
    full = runner.invoke(
        app, ["graph", "export", str(graph_repo_flow), "--flow", "entry"]
    )
    stopped = runner.invoke(
        app,
        [
            "graph",
            "export",
            str(graph_repo_flow),
            "--flow",
            "entry",
            "--stop-at",
            "m.py",
        ],
    )
    assert full.exit_code == 0 and stopped.exit_code == 0
    assert len(stopped.stdout) < len(full.stdout)


def test_graph_export_flow_include_tests_widens_the_picture(graph_repo_flow_hub: Path):
    """The hub fixture is the one with a test-role caller, so the flag can actually change the set."""
    _built(graph_repo_flow_hub)
    without = runner.invoke(
        app, ["graph", "export", str(graph_repo_flow_hub), "--flow", "entry", "--in"]
    )
    with_tests = runner.invoke(
        app,
        [
            "graph",
            "export",
            str(graph_repo_flow_hub),
            "--flow",
            "entry",
            "--in",
            "--include-tests",
        ],
    )
    assert without.exit_code == 0 and with_tests.exit_code == 0
    caller = "tests/test_entry.py::test_entry"
    assert caller in with_tests.stdout and caller not in without.stdout


def test_graph_export_flow_rejects_an_unknown_kind(graph_repo_flow: Path):
    """--kinds is validated on both surfaces, so a typo is an error rather than a narrower tree."""
    _built(graph_repo_flow)
    result = runner.invoke(
        app,
        ["graph", "export", str(graph_repo_flow), "--flow", "entry", "--kinds", "nope"],
    )
    assert result.exit_code != 0
    assert "unknown --kinds" in result.output


@pytest.mark.parametrize(
    "extra",
    [
        ["--in"],
        ["--limit", "5"],
        ["--kinds", "inherits"],
        ["--include-tests"],
        ["--expand-hubs"],
        ["--stop-at", "m.py"],
    ],
)
def test_graph_export_rejects_a_walk_knob_without_flow(graph_repo_flow: Path, extra):
    """They steer the flow walk only; the overview and ego modes dropped them silently."""
    _built(graph_repo_flow)
    result = runner.invoke(app, ["graph", "export", str(graph_repo_flow), *extra])
    assert result.exit_code != 0


@pytest.mark.parametrize("value", [str(MAX_FLOW_DEPTH + 1), "-1"])
def test_graph_export_bounds_its_depth(graph_repo_flow: Path, value: str):
    """The same walk `graph flow` bounds: export ran it unbounded above the cap and tracebacked
    below zero, while the docs promised both surfaces took 0 to 64."""
    _built(graph_repo_flow)
    result = runner.invoke(
        app,
        ["graph", "export", str(graph_repo_flow), "--flow", "entry", "--depth", value],
    )
    assert result.exit_code == 2
    assert "Traceback" not in result.output


def _unreachable(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("the payload build ran before the walk knobs were validated")


def test_graph_export_validates_the_walk_knobs_before_it_builds_the_payload(
    graph_repo_flow: Path, monkeypatch: pytest.MonkeyPatch
):
    """`graph flow` rejects a typo before it queries; export paid a whole payload build first."""
    _built(graph_repo_flow)
    monkeypatch.setattr("auditor.cli.graph.build_payload", _unreachable)
    result = runner.invoke(
        app,
        ["graph", "export", str(graph_repo_flow), "--flow", "entry", "--kinds", "nope"],
    )
    assert result.exit_code != 0
    assert "unknown --kinds" in result.output


def test_graph_export_depth_is_still_legal_without_flow(graph_repo_flow: Path):
    """--depth is not a walk-only knob: it sets the ego export's hop count."""
    _built(graph_repo_flow)
    result = runner.invoke(
        app,
        ["graph", "export", str(graph_repo_flow), "--symbol", "entry", "--depth", "2"],
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout.startswith("digraph")
