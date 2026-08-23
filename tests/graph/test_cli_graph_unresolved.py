"""`auditr graph unresolved` — the queue subcommand mounted from cli/graph_refine.py."""

from pathlib import Path

from _support import cli_json
from typer.testing import CliRunner

from auditor.cli import app

runner = CliRunner()

_HELPER = "def handle():\n    return 1\n"
# `handle()` beside `job.handle()`: one row, and the bare form is the one it keeps
_CALLER = "def use(job):\n    return handle() or job.handle()\n"
_ATTR_CALLER = "def go(job):\n    return job.handle()\n"


def _queue_repo(repo: Path) -> Path:
    """The default one-module repo plus a bare, a both-forms and an attribute-only caller of a
    name only `helper.py` defines, scanned and built."""
    (repo / "helper.py").write_text(_HELPER)
    (repo / "caller.py").write_text(_CALLER)
    (repo / "attr_caller.py").write_text(_ATTR_CALLER)
    assert runner.invoke(app, ["scan", str(repo), "-i"]).exit_code == 0
    assert runner.invoke(app, ["graph", "build", str(repo)]).exit_code == 0
    return repo


def test_unresolved_lists_the_queue_as_json(graph_repo: Path):
    rows = cli_json(
        runner.invoke(
            app, ["graph", "unresolved", str(_queue_repo(graph_repo)), "--json"]
        )
    )
    assert isinstance(rows, list)
    keys = {
        "node_id",
        "fact_kind",
        "name",
        "reason",
        "receiver_root",
        "call_form",
        "candidates",
        "definers",
        "resolution_path",
        "priority",
        "externally_bound",
    }
    assert all(keys == set(r) for r in rows)
    by_key = {(r["node_id"], r["name"]): r for r in rows}
    assert by_key["caller.py::use", "handle"]["call_form"] == "bare"
    assert by_key["caller.py::use", "handle"]["definers"] == ["helper.py::handle"]
    assert by_key["attr_caller.py::go", "handle"]["call_form"] == "attr"


def test_unresolved_filters_by_reason_and_call_form(graph_repo: Path):
    repo = _queue_repo(graph_repo)
    sparse = cli_json(
        runner.invoke(
            app,
            ["graph", "unresolved", str(repo), "--reason", "text_sparse", "--json"],
        )
    )
    assert sparse and all(r["reason"] == "text_sparse" for r in sparse)
    attr = cli_json(
        runner.invoke(
            app, ["graph", "unresolved", str(repo), "--call-form", "attr", "--json"]
        )
    )
    assert all(r["call_form"] == "attr" for r in attr)
    assert ("attr_caller.py::go", "handle") in {(r["node_id"], r["name"]) for r in attr}


def test_unresolved_limit_caps_the_rows(graph_repo: Path):
    rows = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "unresolved",
                str(_queue_repo(graph_repo)),
                "--limit",
                "1",
                "--json",
            ],
        )
    )
    assert len(rows) <= 1


def test_unresolved_on_an_unbuilt_repo_is_empty(graph_repo: Path):
    assert (
        cli_json(runner.invoke(app, ["graph", "unresolved", str(graph_repo), "--json"]))
        == []
    )


def test_build_output_carries_the_unresolved_count(graph_repo: Path):
    assert runner.invoke(app, ["scan", str(graph_repo), "-i"]).exit_code == 0
    built = cli_json(runner.invoke(app, ["graph", "build", str(graph_repo), "--json"]))
    assert isinstance(built["unresolved"], int)
