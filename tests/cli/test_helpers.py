"""cli/helpers.py utility functions tested directly."""

import pytest
import typer

from auditor.cli.helpers import _parse_config_json, _suggest


def test_suggest_returns_closest_match():
    out = _suggest(
        "PY-SEC-DANGEROUS-EVL", ["PY-SEC-DANGEROUS-EVAL", "PY-ASYNC-SYNC-IO"]
    )
    assert out == " Did you mean 'PY-SEC-DANGEROUS-EVAL'?"


def test_suggest_picks_nearest_of_several():
    cands = ["SA-RAW-SQL", "SA-MUTABLE-DEFAULT", "SA-LAZY-DYNAMIC"]
    assert _suggest("SA-RAW-SQ", cands) == " Did you mean 'SA-RAW-SQL'?"


def test_suggest_empty_when_no_close_match():
    assert _suggest("zzzzzzzz", ["PY-SEC-DANGEROUS-EVAL", "PY-ASYNC-SYNC-IO"]) == ""


def test_suggest_empty_candidates():
    assert _suggest("anything", []) == ""


def test_parse_config_json_none():
    assert _parse_config_json(None) is None


def test_parse_config_json_object():
    assert _parse_config_json('{"a": {"b": 1}}') == {"a": {"b": 1}}


def test_parse_config_json_bad_json_exits():
    with pytest.raises(typer.Exit):
        _parse_config_json("{not json")


def test_parse_config_json_non_object_exits():
    with pytest.raises(typer.Exit):
        _parse_config_json("[1, 2]")
