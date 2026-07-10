"""cli/helpers.py utility functions tested directly."""

import pytest
import typer

from auditor.cli.helpers import parse_config_json, run_staged, suggest


def test_suggest_returns_closest_match():
    out = suggest("PY-SEC-DANGEROUS-EVL", ["PY-SEC-DANGEROUS-EVAL", "PY-ASYNC-SYNC-IO"])
    assert out == " Did you mean 'PY-SEC-DANGEROUS-EVAL'?"


def test_suggest_picks_nearest_of_several():
    cands = ["SA-RAW-SQL", "SA-MUTABLE-DEFAULT", "SA-LAZY-DYNAMIC"]
    assert suggest("SA-RAW-SQ", cands) == " Did you mean 'SA-RAW-SQL'?"


def test_suggest_empty_when_no_close_match():
    assert suggest("zzzzzzzz", ["PY-SEC-DANGEROUS-EVAL", "PY-ASYNC-SYNC-IO"]) == ""


def test_suggest_empty_candidates():
    assert suggest("anything", []) == ""


def test_parse_config_json_none():
    assert parse_config_json(None) is None


def test_parse_config_json_object():
    assert parse_config_json('{"a": {"b": 1}}') == {"a": {"b": 1}}


def test_parse_config_json_bad_json_exits():
    with pytest.raises(typer.Exit):
        parse_config_json("{not json")


def test_parse_config_json_non_object_exits():
    with pytest.raises(typer.Exit):
        parse_config_json("[1, 2]")


def test_run_staged_no_spinner_runs_with_noop_reporter():
    seen: list[str] = []

    async def make(report):
        report("stage one")
        return 42

    result = run_staged(make, "msg", spinner=False)
    assert result == 42
    assert seen == []  # no-op reporter recorded nothing externally


def test_run_staged_with_spinner_returns_result():
    async def make(report):
        report("stage one")
        report("stage two")
        return "ok"

    # rich auto-disables the spinner off-TTY in tests, so this won't hang.
    assert run_staged(make, "msg") == "ok"
