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


# ---------------------------------------------------------------------------
# NoAwaitBody — protocol dunder exemption
# ---------------------------------------------------------------------------


def test_no_await_body_protocol_dunder_does_not_fire():
    # __aenter__ is a protocol coroutine — legitimately await-free
    src = "class Ctx:\n    async def __aenter__(self): return self\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


def test_no_await_body_ordinary_method_fires():
    # an ordinary async method with no await must fire
    src = "class Svc:\n    async def setup(self): return self\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" in rule_ids(run_audit(src))


# ---------------------------------------------------------------------------
# NoAwaitBody — abstract/stub body exemptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "pass",
        "...",
        "raise NotImplementedError",
        '"""doc"""',
    ],
    ids=["pass", "ellipsis", "raise-not-impl", "docstring"],
)
def test_no_await_body_stub_body_does_not_fire(body: str) -> None:
    src = f"async def stub():\n    {body}\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


# ---------------------------------------------------------------------------
# NoAwaitBody — framework-managed signatures (route handlers / abstract methods)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "deco",
    ['@app.get("/ping")', '@router.post("/x")', "@app.websocket"],
    ids=["get", "post", "websocket"],
)
def test_no_await_body_route_handler_exempt(deco: str) -> None:
    # a route handler's async-vs-sync choice is framework-managed (event-loop vs threadpool) —
    # regression: orion app.py `async def pong() -> bool: return True` was wrongly flagged
    src = f"{deco}\nasync def handler():\n    return True\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


def test_no_await_body_abstractmethod_exempt() -> None:
    # an abstract coroutine must keep its async signature for subclass overrides, even with a
    # non-stub body
    src = (
        "from abc import abstractmethod\n\n\n"
        "class Base:\n"
        "    @abstractmethod\n"
        "    async def fetch(self) -> int:\n"
        "        return 0\n"
    )
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


# ---------------------------------------------------------------------------
# PY-ASYNC-NO-AWAIT-BODY — complex edge-case tests
# ---------------------------------------------------------------------------


def test_no_await_body_async_generator_not_flagged() -> None:
    # An async generator (yield inside async def) MUST remain async so callers can
    # `async for` over it; the absence of an explicit await is NOT a mistake.
    src = "async def g():\n    yield 1\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


def test_no_await_body_aexit_protocol_not_flagged() -> None:
    # __aexit__ is in the async protocol set — must not fire even with a real body.
    src = "class Ctx:\n    async def __aexit__(self, *a):\n        return False\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


def test_no_await_body_abstractmethod_non_stub_body_not_flagged() -> None:
    # @abstractmethod with a non-stub body (return 0, not just pass/...) must not fire —
    # subclass overrides need the async signature even when the base has a concrete fallback.
    src = (
        "from abc import abstractmethod\n\n\n"
        "class Base:\n"
        "    @abstractmethod\n"
        "    async def fetch(self) -> int:\n"
        "        return 0\n"
    )
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


def test_no_await_body_async_with_counts() -> None:
    # async-with is an async construct; a function using it has no need for an explicit await.
    src = "async def f():\n    async with x() as y:\n        pass\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


def test_no_await_body_async_for_counts() -> None:
    # async-for is an async construct; using it alone is sufficient to suppress the rule.
    src = "async def f():\n    async for i in g():\n        pass\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


@pytest.mark.parametrize(
    "deco",
    ['@router.post("/x")', '@app.get("/ping")'],
    ids=["router-post", "app-get"],
)
def test_no_await_body_route_handler_variants_not_flagged(deco: str) -> None:
    # Various route-handler decorators suppress the rule; async-vs-sync is framework-managed.
    src = f"{deco}\nasync def h():\n    return 1\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src))


def test_no_await_body_plain_function_without_decorator_flagged() -> None:
    # Same body as the route handler test above, but WITHOUT the decorator → must fire.
    src = "async def h():\n    return 1\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" in rule_ids(run_audit(src))


def test_no_await_body_nested_inner_flagged_outer_not() -> None:
    # The outer async function has an await → clean.
    # The nested inner async function has no await → FLAGGED.
    # The finding's line must point to the inner function's def line, not the outer.
    src = "async def a():\n    await x()\n    async def b():\n        return 1\n"
    result = run_audit(src)
    findings = [f for f in result.findings if f.rule_id == "PY-ASYNC-NO-AWAIT-BODY"]
    assert len(findings) == 1, (
        f"expected exactly one PY-ASYNC-NO-AWAIT-BODY finding, got {findings}"
    )
    # inner `async def b` starts at line 3
    assert findings[0].line == 3, (
        f"finding should point at inner function (line 3), got line {findings[0].line}"
    )
