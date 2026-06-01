"""Detectors in async_rules.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["async_rules"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


def test_awaited_call_not_flagged_as_sync_io():
    # an awaited call does not block the loop, even if its name ends in .write/.read
    bad = "async def f(conn):\n    conn.write(data)\n"  # not awaited -> flagged
    good = "async def f(store):\n    await store.write(data)\n"  # awaited -> clean
    assert "PY-ASYNC-SYNC-IO" in rule_ids(run_audit(bad))
    assert "PY-ASYNC-SYNC-IO" not in rule_ids(run_audit(good))
