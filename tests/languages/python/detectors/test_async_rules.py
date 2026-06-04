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


def test_async_generator_not_flagged_no_await_body():
    # an async generator (has yield, consumed via `async for`) must stay async even
    # without an internal await — not a "make it sync" candidate.
    src = "async def stream(items):\n    for x in items:\n        yield x\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


def test_async_no_await_still_flags_plain_coroutine():
    src = "async def compute(x):\n    return x + 1\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" in rule_ids(run_audit(src))


def test_sequential_awaits_ignores_await_in_the_iterable():
    # the await is in the for-iterable (evaluated once), not the body — not a gather opportunity
    good = (
        "async def f():\n    for row in (await fetch()).rows:\n        process(row)\n"
    )
    assert "PY-ASYNC-SEQUENTIAL-AWAITS" not in rule_ids(run_audit(good))
    # a real per-iteration await in the body still flags
    bad = "async def f(xs):\n    for x in xs:\n        await g(x)\n"
    assert "PY-ASYNC-SEQUENTIAL-AWAITS" in rule_ids(run_audit(bad))
