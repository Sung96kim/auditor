"""The loop's spine: the run slots, the quiet window, the pauses and the retry budget."""

import asyncio
import threading

import pytest

from auditor.graph.refine.models import NodePair
from auditor.observer.budget import BudgetState
from auditor.observer.events import Event
from auditor.observer.scheduling import (
    EventFeed,
    LoopState,
    Pauses,
    QueueFeed,
    Retries,
    RunSlots,
    debounced,
    pause_of,
)


class ScriptedFeed(EventFeed):
    """Hands back one scripted group per `take`, then nothing: the debounce's own clock."""

    def __init__(self, *groups: tuple[Event, ...]) -> None:
        self.groups = list(groups)
        self.waits: list[float] = []

    async def take(self, timeout: float) -> tuple[Event, ...]:
        self.waits.append(timeout)
        return self.groups.pop(0) if self.groups else ()


class FloodFeed(EventFeed):
    """A feed that never goes quiet: the edit stream the window ceiling exists for."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def take(self, timeout: float) -> tuple[Event, ...]:
        self.waits.append(timeout)
        return (_event("flood.py"),)


def _event(path: str) -> Event:
    return Event(repo="/r", paths=(path,))


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


async def test_the_window_restarts_on_every_event_and_ends_on_quiet():
    """Spec 8.3's "one batch per quiet window": a burst and its Stop set are one batch."""
    feed = ScriptedFeed((_event("a.py"),), (_event("b.py"),), (_event("c.py"),))
    batch = await debounced(feed, seconds=20.0, timeout=1.0)
    assert [e.paths[0] for e in batch] == ["a.py", "b.py", "c.py"]
    assert feed.waits == [1.0, 20.0, 20.0, 20.0]


async def test_a_feed_that_never_goes_quiet_still_yields_inside_the_cap():
    """A sustained edit stream would otherwise keep `tick` from ever returning to the ladder."""
    feed = FloodFeed()
    batch = await debounced(feed, seconds=20.0, timeout=1.0, max_seconds=60.0)
    assert len(batch) == 4
    assert sum(feed.waits[1:]) == 60.0


async def test_a_quiet_window_hands_back_nothing_rather_than_an_empty_batch():
    assert await debounced(ScriptedFeed(), seconds=20.0, timeout=0.0) == ()


async def test_a_debounce_of_zero_takes_the_first_group_and_stops():
    """`debounce_seconds = 0` is a user who wants every post assessed on its own."""
    feed = ScriptedFeed((_event("a.py"),), (_event("b.py"),))
    assert len(await debounced(feed, seconds=0.0, timeout=1.0)) == 1


async def test_the_queue_feed_takes_everything_already_waiting_in_one_call():
    """The daemon's drain hands a whole spool over; a feed that returned one would debounce it."""
    feed = QueueFeed()
    feed.offer([_event("a.py"), _event("b.py")])
    await asyncio.sleep(0)
    assert len(await feed.take(1.0)) == 2
    assert await feed.take(0.0) == ()


async def test_a_feed_built_off_the_loop_holds_what_another_thread_offers_it():
    """The daemon's drain thread has no running loop, so `QueueFeed` must not ask for one."""
    feed = QueueFeed()
    done = threading.Event()

    def drain() -> None:
        feed.offer([_event("a.py")])
        done.set()

    threading.Thread(target=drain).start()
    done.wait(5)
    assert [e.paths[0] for e in await feed.take(1.0)] == ["a.py"]
    feed.offer([_event("b.py")])
    await asyncio.sleep(0)
    assert [e.paths[0] for e in await feed.take(1.0)] == ["b.py"]


@pytest.mark.parametrize(
    ("error", "state", "resumes"),
    [
        ("paused:auth", LoopState.PAUSED_AUTH, 1_000.0),
        ("paused:ratelimit until 500.0", LoopState.PAUSED_RATELIMIT, 500.0),
        ("paused:ratelimit until None", LoopState.PAUSED_RATELIMIT, 400.0),
        ("paused:ratelimit", LoopState.PAUSED_RATELIMIT, 400.0),
        ("paused:billing", LoopState.PAUSED_BUDGET, None),
    ],
    ids=["auth", "with a reset", "an unreadable reset", "no reset", "billing"],
)
def test_a_stopped_run_s_error_names_the_pause_and_its_deadline(error, state, resumes):
    """C45: the runner writes a sentence, and the loop needs the instant out of it."""
    pause = pause_of(error, now=100.0, minutes=5.0)
    assert (pause.state, pause.resumes_at) == (state, resumes)


@pytest.mark.parametrize("error", ["", None, "invalid_request", "server_error"])
def test_an_error_that_is_not_a_pause_leaves_the_loop_running(error):
    assert pause_of(error, now=100.0) is None


def _budget(*, spent: float = 0.0) -> BudgetState:
    return BudgetState(spent_usd=spent, max_cost_usd_per_day=2.0, max_runs_per_day=40)


def test_a_rate_limit_clears_itself_the_moment_its_deadline_passes():
    """Spec 8.4 holds it in RepoLoop memory, so nothing has to write or read it back."""
    pauses = Pauses()
    pauses.apply(pause_of("paused:ratelimit until 500.0", now=100.0))
    assert pauses.state(budget=_budget(), now=499.0) is LoopState.PAUSED_RATELIMIT
    assert pauses.state(budget=_budget(), now=500.0) is None
    assert pauses.resumes_at is None


def test_auth_outranks_every_other_pause():
    """A loop that cannot log in must not report a budget it can never spend."""
    pauses = Pauses()
    pauses.apply(pause_of("paused:auth", now=0.0))
    assert pauses.state(budget=_budget(spent=2.0), now=0.0) is LoopState.PAUSED_AUTH
    pauses.cleared()
    assert pauses.state(budget=_budget(spent=2.0), now=0.0) is LoopState.PAUSED_BUDGET
    assert pauses.state(budget=_budget(), now=0.0) is None


def test_an_auth_pause_expires_and_a_run_that_reached_the_model_clears_it():
    """Nothing runs while `paused:auth` holds, so without a deadline nothing could ever clear it."""
    pauses = Pauses()
    pauses.apply(pause_of("paused:auth", now=0.0))
    assert pauses.state(budget=_budget(), now=899.0) is LoopState.PAUSED_AUTH
    assert pauses.state(budget=_budget(), now=900.0) is None
    pauses.apply(pause_of("paused:auth", now=900.0))
    pauses.authenticated()
    assert pauses.state(budget=_budget(), now=900.0) is None


def test_a_target_is_re_queued_once_and_then_dropped():
    """Spec 8.5's "re-queued once", as a loop-side budget rather than a database write (Q11)."""
    pair = NodePair(node_id="a.py::f", name="load")
    other = NodePair(node_id="b.py::g", name="save")
    retries = Retries()
    assert retries.keep((pair, other)) == (pair, other)
    retries.aborted((pair,))
    assert retries.keep((pair, other)) == (pair, other)
    retries.aborted((pair,))
    assert retries.keep((pair, other)) == (other,)
