"""Detectors in security/injection.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["security/injection"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


# a subscript of a *local* (a config dict, a list) is not external data — must not flag
_SQL_BENIGN = [
    'def q(cur):\n    cfg = {"t": "users"}\n    cur.execute("SELECT * FROM " + cfg["t"])\n',  # local dict
    'TABLES = {"u": "users"}\ndef q(cur):\n    cur.execute("SELECT * FROM " + TABLES["u"])\n',  # module const
    'def q(cur):\n    cols = ["id"]\n    cur.execute("SELECT " + cols[0] + " FROM t")\n',  # local list
]


@pytest.mark.parametrize("src", _SQL_BENIGN)
def test_sql_string_build_ignores_local_subscript(src):
    assert "PY-SEC-SQL-STRING-BUILD" not in rule_ids(run_audit(src))


# a subscript of a request/environment source IS external — must flag
_SQL_EXTERNAL = [
    'def q(cur, request):\n    cur.execute("SELECT * FROM " + request.args["t"])\n',
    'import os\ndef q(cur):\n    cur.execute("SELECT * FROM " + os.environ["T"])\n',
    'def q(cur, name):\n    cur.execute("SELECT * FROM " + name)\n',  # bare param
]


@pytest.mark.parametrize("src", _SQL_EXTERNAL)
def test_sql_string_build_flags_external_subscript(src):
    assert "PY-SEC-SQL-STRING-BUILD" in rule_ids(run_audit(src))
