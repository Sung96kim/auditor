"""Spec 8.4's run slots: one per repo, two across the daemon, and the waiting a cap means."""

import asyncio

import pytest

from auditor.observer.scheduling import RunSlots


async def test_one_run_per_repo_and_a_second_waits():
    """Spec 8.4's per-repo cap: the second caller blocks rather than being evicted."""
    slots = RunSlots()
    await slots.acquire("a")
    assert slots.held("a") == 1
    second = asyncio.create_task(slots.acquire("a"))
    await asyncio.sleep(0)
    assert not second.done()
    await slots.release("a")
    await asyncio.wait_for(second, timeout=1.0)
    assert slots.held("a") == 1
    await slots.release("a")
    assert slots.held("a") == 0


async def test_two_globally_across_different_repos():
    """ "Globally" is across repos in one daemon, which is why the daemon owns the instance."""
    slots = RunSlots()
    await slots.acquire("a")
    await slots.acquire("b")
    third = asyncio.create_task(slots.acquire("c"))
    await asyncio.sleep(0)
    assert not third.done()
    await slots.release("a")
    await asyncio.wait_for(third, timeout=1.0)
    await slots.release("b")
    await slots.release("c")
    assert slots.held("c") == 0


async def test_the_slot_context_gives_the_slot_back_even_when_the_run_raises():
    slots = RunSlots()
    with pytest.raises(RuntimeError):
        async with slots.slot("a"):
            assert slots.held("a") == 1
            raise RuntimeError("the run failed")
    assert slots.held("a") == 0


async def test_releasing_a_repo_that_holds_nothing_is_a_no_op():
    """A loop that lost track must not hand back a slot it never took."""
    slots = RunSlots()
    await slots.release("never-acquired")
    assert slots.held("never-acquired") == 0
    await slots.acquire("a")
    await slots.release("a")
    await slots.release("a")
    assert slots.held("a") == 0
    await slots.acquire("b")
    await slots.acquire("c")
    assert slots.held("b") == 1
