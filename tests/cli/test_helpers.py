"""cli/helpers.py utility functions tested directly."""

from auditor.cli.helpers import _suggest


def test_suggest_returns_closest_match():
    out = _suggest("PY-SEC-DANGEROUS-EVL", ["PY-SEC-DANGEROUS-EVAL", "PY-ASYNC-SYNC-IO"])
    assert out == " Did you mean 'PY-SEC-DANGEROUS-EVAL'?"


def test_suggest_picks_nearest_of_several():
    cands = ["SA-RAW-SQL", "SA-MUTABLE-DEFAULT", "SA-LAZY-DYNAMIC"]
    assert _suggest("SA-RAW-SQ", cands) == " Did you mean 'SA-RAW-SQL'?"


def test_suggest_empty_when_no_close_match():
    assert _suggest("zzzzzzzz", ["PY-SEC-DANGEROUS-EVAL", "PY-ASYNC-SYNC-IO"]) == ""


def test_suggest_empty_candidates():
    assert _suggest("anything", []) == ""
