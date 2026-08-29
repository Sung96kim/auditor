"""`auditr graph eval`: the option refusals, the exit codes, and the gate it opens."""

import json
from pathlib import Path

import pytest
from _support import cli_json, invoke, one_line
from graph._support import (
    ClaudeShaped,
    EvalClaude,
    FailingClaude,
    WrongEvalClaude,
    render_text,
)

from auditor.cli.render import render_graph_eval
from auditor.graph.refine import drive
from auditor.graph.refine.models import RunnerKind
from auditor.graph.refine.payloads import EvalActivation, EvalReport

#: a bar this package's one-truth strata can clear, so the gate is testable without 73 trials
LOW_BAR = "0.2"


@pytest.fixture
def eval_runner(claude_runner: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The Claude choice logic, driving the row-reading fake instead of the SDK."""
    claude_runner.setitem(drive.RUNNERS, RunnerKind.CLAUDE, EvalClaude)
    return claude_runner


def _eval(repo: Path, *args: str):
    return invoke("graph", "eval", str(repo), *args, "--json")


def test_an_eval_measures_every_suite_and_exits_zero(eval_repo, eval_runner):
    payload = cli_json(_eval(eval_repo, "--sample", "2"))
    assert payload["runner"] == "claude"
    assert payload["runs"] == payload["plan"]["runs_planned"] > 0
    keys = {f"{s['suite']}/{s['stratum']}" for s in payload["suites"]}
    assert keys == {
        "add/same-module",
        "add/direct-import",
        "add/neither",
        "collision/all",
        "negative/all",
        "decoy/all",
    }


def test_the_rows_are_stored_where_the_gate_reads_them(eval_repo, eval_runner):
    _eval(eval_repo, "--sample", "2")
    listed = cli_json(invoke("graph", "log", str(eval_repo), "--json"))
    assert {row["trigger_kind"] for row in listed["runs"]} == {"eval"}
    rows = cli_json(invoke("graph", "refinements", "list", str(eval_repo), "--json"))
    assert rows["rows"] == []


def test_the_same_seed_reports_the_same_numbers(eval_repo, eval_runner):
    args = ("--suite", "add", "--sample", "2", "--seed", "7")
    first = cli_json(_eval(eval_repo, *args))
    second = cli_json(_eval(eval_repo, *args))
    assert [s["n"] for s in first["suites"]] == [s["n"] for s in second["suites"]]
    assert [s["correct"] for s in first["suites"]] == [
        s["correct"] for s in second["suites"]
    ]


def test_the_fixtures_suite_is_refused_naming_the_follow_up(eval_repo, eval_runner):
    result = _eval(eval_repo, "--suite", "fixtures")
    assert result.exit_code == 2
    assert "tests/fixtures/graph_eval/" in result.output


def test_an_unknown_suite_is_exit_two(eval_repo, eval_runner):
    result = _eval(eval_repo, "--suite", "nope")
    assert result.exit_code == 2
    assert "unknown --suite" in result.output


@pytest.mark.parametrize("size", ["0", "501"])
def test_a_sample_outside_the_range_is_exit_two(eval_repo, eval_runner, size):
    assert _eval(eval_repo, "--sample", size).exit_code == 2


def test_a_codex_runner_is_refused_at_exit_one(eval_repo, eval_runner):
    result = _eval(eval_repo, "--runner", "codex")
    assert result.exit_code == 1
    assert "S12" in result.output


def test_without_the_extra_the_refusal_names_it(eval_repo, monkeypatch):
    monkeypatch.setattr(drive, "SDK_AVAILABLE", False)
    result = _eval(eval_repo, "--runner", "claude")
    assert result.exit_code == 1
    assert "observer-claude" in result.output


def test_a_run_that_aborts_exits_one_with_the_partial_report(eval_repo, claude_runner):
    claude_runner.setitem(drive.RUNNERS, RunnerKind.CLAUDE, FailingClaude)
    result = _eval(eval_repo, "--suite", "add", "--sample", "1")
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["runs"] < payload["plan"]["runs_planned"]
    assert any("aborted" in line for line in payload["notes"]["stopped"])


def test_the_human_table_marks_what_it_proved(eval_repo, eval_runner, monkeypatch):
    monkeypatch.setenv("AUDITOR_USER_OBSERVER__TUNING__MIN_PRECISION", LOW_BAR)
    payload = EvalReport.model_validate(cli_json(_eval(eval_repo, "--sample", "2")))
    shown = render_text(render_graph_eval, payload, width=120)
    assert "add/same-module" in shown and "OK" in shown
    assert "runs planned" in shown
    assert "tier B active for same-module, direct-import, neither" in shown


@pytest.mark.parametrize(
    ("activation", "expected"),
    [
        (
            EvalActivation(proven=("decoy/all",), resolve_ambiguous=True),
            "resolve_ambiguous: yes; tier B active for no stratum",
        ),
        (
            EvalActivation(proven=("add/neither",), tier_b=("neither",)),
            "resolve_ambiguous: no; tier B active for neither",
        ),
    ],
)
def test_the_activation_line_says_what_the_gate_says(activation, expected):
    """Both halves come off the policy, so neither can be re-derived from `proven` alone."""
    payload = EvalReport(
        runner=RunnerKind.CLAUDE,
        model="haiku",
        min_precision=0.95,
        activation=activation,
    )
    assert expected in render_text(render_graph_eval, payload, width=120)


def test_the_add_suite_alone_proves_its_strata_and_activates_nothing(
    eval_repo, eval_runner, monkeypatch
):
    """Tier B needs the collision control too, so a report that read only `add/` said active
    where the ledger would store pending."""
    monkeypatch.setenv("AUDITOR_USER_OBSERVER__TUNING__MIN_PRECISION", LOW_BAR)
    payload = EvalReport.model_validate(
        cli_json(_eval(eval_repo, "--suite", "add", "--sample", "2"))
    )
    assert "add/same-module" in payload.activation.proven
    assert payload.activation.tier_b == ()
    shown = render_text(render_graph_eval, payload, width=120)
    assert "tier B active for no stratum" in shown


def test_the_human_table_says_what_it_could_not_prove(eval_repo, eval_runner):
    payload = EvalReport.model_validate(
        cli_json(_eval(eval_repo, "--suite", "add", "--sample", "2"))
    )
    shown = render_text(render_graph_eval, payload, width=120)
    assert "unprovable as drawn, add/same-module: 2 trials, below the 73" in one_line(
        shown
    )
    assert "73 flawless trials" in shown
    assert "tier B active for no stratum" in shown


def test_a_dry_run_prints_the_plan_and_opens_no_run(eval_repo, eval_runner):
    payload = cli_json(_eval(eval_repo, "--sample", "2", "--dry-run"))
    assert payload["plan"]["runs_planned"] == 6
    assert payload["plan"]["max_budget_usd_per_run"] == 0.25
    assert payload["suites"] == [] and payload["runs"] == 0
    assert cli_json(invoke("graph", "log", str(eval_repo), "--json"))["runs"] == []


def test_a_human_dry_run_shows_the_whole_plan(eval_repo, eval_runner):
    payload = EvalReport.model_validate(
        cli_json(_eval(eval_repo, "--sample", "2", "--dry-run"))
    )
    shown = one_line(render_text(render_graph_eval, payload, width=120))
    assert (
        "6 runs planned for haiku, each capped at $0.25, up to $1.50 against the "
        "$12.00 eval ceiling" in shown
    )
    assert "add/same-module: 2 trials" in shown
    assert "(nothing measured)" in shown


def test_the_human_path_prints_what_it_may_spend_before_it_spends(
    eval_repo, eval_runner
):
    """The plan is only a cost guard if it is read while there is still money to save."""
    result = invoke("graph", "eval", str(eval_repo), "--suite", "add", "--sample", "1")
    assert result.exit_code == 0
    assert (
        "3 runs planned, each capped at $0.25, up to $0.75 against the "
        "$12.00 eval ceiling" in one_line(result.output)
    )


def test_an_empty_suite_is_named_rather_than_counted(refine_repo, eval_runner):
    """`refine_repo` has no externally bound queue row, so the collision suite draws nothing."""
    payload = cli_json(
        invoke("graph", "eval", str(refine_repo), "--suite", "collision", "--json")
    )
    assert payload["notes"]["empty"] == ["collision/all"]
    assert payload["suites"] == []


def test_a_proving_eval_lets_a_tier_b_correction_land_active(
    eval_repo, eval_runner, monkeypatch
):
    """The gate end to end: `caller.main -> helper.read_event` is a `neither`-shaped tier B row."""
    monkeypatch.setenv("AUDITOR_USER_OBSERVER__TUNING__MIN_PRECISION", LOW_BAR)
    proven = cli_json(_eval(eval_repo, "--sample", "2"))["activation"]
    assert "add/neither" in proven["proven"] and "collision/all" in proven["proven"]
    eval_runner.setitem(drive.RUNNERS, RunnerKind.CLAUDE, ClaudeShaped)
    landed = cli_json(invoke("graph", "refine", "", str(eval_repo), "--json"))
    assert [v["status"] for v in landed["committed"]] == ["active"]


def test_a_regressing_eval_takes_that_back(eval_repo, eval_runner, monkeypatch):
    """P1: the newest row per key governs, so a failing suite closes the gate again."""
    monkeypatch.setenv("AUDITOR_USER_OBSERVER__TUNING__MIN_PRECISION", LOW_BAR)
    first = cli_json(_eval(eval_repo, "--sample", "2"))
    assert "add/neither" in first["activation"]["proven"]
    eval_runner.setitem(drive.RUNNERS, RunnerKind.CLAUDE, WrongEvalClaude)
    regressed = cli_json(_eval(eval_repo, "--sample", "2"))
    assert "add/neither" not in regressed["activation"]["proven"]
    assert all(s["correct"] == 0 for s in regressed["suites"] if s["suite"] == "add")
    eval_runner.setitem(drive.RUNNERS, RunnerKind.CLAUDE, ClaudeShaped)
    landed = cli_json(invoke("graph", "refine", "", str(eval_repo), "--json"))
    assert [v["status"] for v in landed["committed"]] == ["pending"]
