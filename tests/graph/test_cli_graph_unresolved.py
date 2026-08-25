"""`auditr graph unresolved` — the queue subcommand mounted from cli/graph_refine.py."""

import io
from pathlib import Path

import pytest
from _support import cli_json
from pydantic import ValidationError
from rich.console import Console
from typer.testing import CliRunner

from auditor.cli import app
from auditor.cli.render import render_graph_unresolved
from auditor.graph.model import QUEUE_ID_CAP
from auditor.graph.payloads import QueueReport, QueueRowPayload

runner = CliRunner()


def _render(payload: QueueReport, *, filtered: bool = False) -> str:
    buf = io.StringIO()
    render_graph_unresolved(Console(file=buf, width=100), payload, filtered=filtered)
    return buf.getvalue()


_HELPER = "def handle():\n    return 1\n"
# `handle()` beside `job.handle()`: one row, and the bare form is the one it keeps
_CALLER = "def use(job):\n    return handle() or job.handle()\n"
_ATTR_CALLER = "def go(job):\n    return job.handle()\n"
_EXTERNAL_CALLER = "import re\ndef find(s):\n    return re.handle(s)\n"


def _queue_repo(repo: Path) -> Path:
    """The default one-module repo plus a bare, a both-forms, an attribute-only and an
    externally bound caller of a name only `helper.py` defines, scanned and built."""
    (repo / "helper.py").write_text(_HELPER)
    (repo / "caller.py").write_text(_CALLER)
    (repo / "attr_caller.py").write_text(_ATTR_CALLER)
    (repo / "ext_caller.py").write_text(_EXTERNAL_CALLER)
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
        "candidates_count",
        "definers",
        "definers_count",
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
    """The cap has to do observable work: the fixture produces more rows than the limit, and the
    row that survives is the worst one, not an arbitrary one."""
    repo = _queue_repo(graph_repo)
    everything = cli_json(
        runner.invoke(app, ["graph", "unresolved", str(repo), "--json"])
    )
    assert len(everything) > 1
    capped = cli_json(
        runner.invoke(app, ["graph", "unresolved", str(repo), "--limit", "1", "--json"])
    )
    assert len(capped) == 1
    assert capped[0] == everything[0]


@pytest.mark.parametrize(
    ("flag", "value"), [("--reason", "ambigous_name"), ("--call-form", "barre")]
)
def test_an_unknown_filter_value_is_rejected_not_silently_empty(
    graph_repo: Path, flag: str, value: str
):
    """A typo must not read as an empty queue: the sibling `graph export --format` already
    refuses an unknown value."""
    result = runner.invoke(app, ["graph", "unresolved", str(graph_repo), flag, value])
    assert result.exit_code != 0
    assert value in result.output


def test_a_limit_below_one_is_rejected(graph_repo: Path):
    """`--limit 0` used to render as an empty queue and `--limit -1` silently dropped the last
    row."""
    result = runner.invoke(
        app, ["graph", "unresolved", str(graph_repo), "--limit", "0"]
    )
    assert result.exit_code != 0


def test_no_external_drops_the_externally_bound_rows(graph_repo: Path):
    repo = _queue_repo(graph_repo)
    shown = cli_json(runner.invoke(app, ["graph", "unresolved", str(repo), "--json"]))
    assert any(r["externally_bound"] for r in shown)
    hidden = cli_json(
        runner.invoke(
            app, ["graph", "unresolved", str(repo), "--no-external", "--json"]
        )
    )
    assert hidden and not any(r["externally_bound"] for r in hidden)


def test_the_id_lists_are_capped_with_their_true_totals(graph_repo: Path):
    """A name many modules define: the payload carries exactly the cap, and the true count. The
    fixture size is a literal, so raising the cap past it fails here instead of tracking it."""
    definers = 12
    assert definers > QUEUE_ID_CAP, "the fixture must define more than the cap"
    for i in range(definers):
        (graph_repo / f"d{i}.py").write_text("def handle():\n    return 1\n")
    (graph_repo / "caller.py").write_text("def use():\n    return handle()\n")
    assert runner.invoke(app, ["scan", str(graph_repo), "-i"]).exit_code == 0
    assert runner.invoke(app, ["graph", "build", str(graph_repo)]).exit_code == 0
    rows = cli_json(
        runner.invoke(app, ["graph", "unresolved", str(graph_repo), "--json"])
    )
    row = next(
        r for r in rows if (r["node_id"], r["name"]) == ("caller.py::use", "handle")
    )
    assert len(row["definers"]) == QUEUE_ID_CAP
    assert row["definers_count"] == definers


def test_a_queue_column_no_model_declares_fails_loudly(graph_repo: Path):
    """`graph_unresolved` is read with `SELECT *`, so `extra="forbid"` is what stops a new column
    being dropped between the table and both surfaces."""
    rows = cli_json(
        runner.invoke(
            app, ["graph", "unresolved", str(_queue_repo(graph_repo)), "--json"]
        )
    )
    assert rows, "the fixture repo produced no unresolved rows"
    assert QueueRowPayload.of(rows[0])  # the stored shape still validates
    with pytest.raises(ValidationError, match="run_id"):
        QueueRowPayload.of({**rows[0], "run_id": 7})


def test_a_stored_priority_survives_the_payload_round_trip():
    """`UnresolvedRow._derive_priority` refills a missing priority from the reason and the call
    form, so a value it would never derive is the only one that tells preserved from recomputed:
    S3's `flow_leaf` bump reserves 0, and this row would otherwise derive 2."""
    row = {
        "node_id": "m.py::use",
        "name": "handle",
        "reason": "unimportable_name",
        "fact_kind": "attr_callee",
        "receiver_root": None,
        "call_form": "bare",
        "candidates": [],
        "definers": [],
        "resolution_path": [],
        "priority": 0,
        "externally_bound": False,
    }
    assert QueueRowPayload.of(row).priority == 0
    assert QueueRowPayload.of({**row, "priority": None}).priority == 2


def test_an_empty_queue_and_an_empty_filter_read_differently():
    """Four causes used to render one message; only the never-built one may name the build."""
    empty = _render(QueueReport(()))
    filtered = _render(QueueReport(()), filtered=True)
    assert "graph build" in empty
    assert "graph build" not in filtered
    assert "filter" in filtered


def test_unresolved_on_an_unbuilt_repo_is_empty(graph_repo: Path):
    assert (
        cli_json(runner.invoke(app, ["graph", "unresolved", str(graph_repo), "--json"]))
        == []
    )


def test_build_output_carries_the_unresolved_count(graph_repo: Path):
    assert runner.invoke(app, ["scan", str(graph_repo), "-i"]).exit_code == 0
    built = cli_json(runner.invoke(app, ["graph", "build", str(graph_repo), "--json"]))
    assert isinstance(built["unresolved"], int)
