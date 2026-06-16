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
# SequentialAwaits — ordered-sink exclusion (await writes to a with-bound resource)
# ---------------------------------------------------------------------------


def test_sequential_awaits_streaming_write_to_context_resource_not_flagged():
    # the canonical false positive: streaming chunks to a file handle bound by `async with`.
    # The writes MUST be ordered — gathering would corrupt the file — so don't suggest gather.
    src = (
        "async def download(client, obj, path):\n"
        "    async with aiofile.async_open(path, mode='wb') as f:\n"
        "        async for chunk in client.read_chunks(obj):\n"
        "            await f.write(chunk)\n"
    )
    assert "PY-ASYNC-SEQUENTIAL-AWAITS" not in rule_ids(run_audit(src))


def test_sequential_awaits_sync_for_write_to_context_resource_not_flagged():
    # same exclusion for a plain `for` writing to a `with`-bound resource
    src = (
        "async def dump(chunks, path):\n"
        "    with open(path, 'wb') as f:\n"
        "        for chunk in chunks:\n"
        "            await f.write(chunk)\n"
    )
    assert "PY-ASYNC-SEQUENTIAL-AWAITS" not in rule_ids(run_audit(src))


def test_sequential_awaits_tuple_bound_context_resource_not_flagged():
    # `async with x() as (reader, writer)` — both names are ordered resources
    src = (
        "async def pipe(src):\n"
        "    async with open_pair() as (reader, writer):\n"
        "        async for item in src:\n"
        "            await writer.send(item)\n"
    )
    assert "PY-ASYNC-SEQUENTIAL-AWAITS" not in rule_ids(run_audit(src))


def test_sequential_awaits_mixed_independent_await_still_flagged():
    # one await writes to the with-bound file (ordered), but another is an independent fetch —
    # since NOT every await is an ordered-sink write, the gather opportunity stands → still flags
    src = (
        "async def f(client, obj, path, xs):\n"
        "    async with aiofile.async_open(path) as out:\n"
        "        for x in xs:\n"
        "            data = await fetch(x)\n"
        "            await out.write(data)\n"
    )
    assert "PY-ASYNC-SEQUENTIAL-AWAITS" in rule_ids(run_audit(src))


def test_sequential_awaits_free_call_in_with_block_still_flagged():
    # being inside a `with` doesn't grant immunity: a free `await fetch(x)` (no with-bound
    # receiver) is still a genuine gather candidate
    src = (
        "async def f(xs):\n"
        "    async with session() as s:\n"
        "        for x in xs:\n"
        "            await fetch(x)\n"
    )
    assert "PY-ASYNC-SEQUENTIAL-AWAITS" in rule_ids(run_audit(src))


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


# ---------------------------------------------------------------------------
# Obscure edge-case tests — discovered+pinned (run to characterize, then asserted)
# ---------------------------------------------------------------------------


def test_no_await_body_await_in_listcomp_not_flagged() -> None:
    # CORRECT: `async def f(xs): return [await g(x) for x in xs]`
    # `_nodes_excluding_nested_funcs` walks into the ListComp and finds the Await node
    # (a list comprehension is NOT an (Async)FunctionDef, so it is not excluded) →
    # `_has_async_construct` returns True → rule does NOT fire.
    src = "async def f(xs):\n    return [await g(x) for x in xs]\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src)), (
        "await inside a list comprehension is visible to _has_async_construct — must NOT flag"
    )


def test_no_await_body_await_only_in_nested_async_def_outer_flagged() -> None:
    # Regression: `async def a(): async def b(): await g(); return 1`
    # Outer `a` has no await in its OWN body; the only await belongs to inner `b`.
    # _has_async_construct / _is_async_generator now skip top-level statements that are
    # themselves (Async)FunctionDef before recursing, so the inner `b`'s await no longer
    # leaks through to the outer. Outer `a` is correctly flagged (it could be made sync —
    # it only defines a nested coroutine and returns a constant). The inner `b` awaits, so
    # only `a` fires.
    src = "async def a():\n    async def b():\n        await g()\n    return 1\n"
    findings = [
        f for f in run_audit(src).findings if f.rule_id == "PY-ASYNC-NO-AWAIT-BODY"
    ]
    assert len(findings) == 1, (
        f"expected exactly one PY-ASYNC-NO-AWAIT-BODY (outer 'a'), got {findings}"
    )
    assert findings[0].line == 1, (
        f"finding should point at outer 'a' (line 1), got line {findings[0].line}"
    )


def test_no_await_body_overload_stub_not_flagged() -> None:
    # CORRECT: `@overload\nasync def f(x: int) -> int: ...`
    # The body is a single Expr(Constant(...)) statement — `_is_abstract_or_stub` returns
    # True (the `...` form) → rule is skipped. The @overload decorator is not checked by
    # name; it is the `...` body that grants the exemption.
    src = "from typing import overload\n@overload\nasync def f(x: int) -> int: ...\n"
    assert "PY-ASYNC-NO-AWAIT-BODY" not in rule_ids(run_audit(src)), (
        "@overload stub with '...' body is exempted via _is_abstract_or_stub — must NOT flag"
    )
