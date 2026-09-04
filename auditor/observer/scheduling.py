"""How many refinement runs the daemon lets happen at once (spec 8.4).

Everything here decides *when* the loop may act. What it does when it may is `loop.py`, and every
value below is computed from an injected clock, so a day boundary and a rate-limit deadline are
testable without sleeping.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from auditor.discovery import FileDiscovery, git_head, git_status_paths
from auditor.graph.refine.models import Checkout, NodePair
from auditor.observer.budget import BudgetState
from auditor.observer.events import Event
from auditor.user_settings import (
    DEBOUNCE_WINDOW_CAP,
    DEFAULT_AUTH_MINUTES,
    DEFAULT_ERROR_SECONDS,
    DEFAULT_RATELIMIT_MINUTES,
    FEED_EVENT_CAP,
    MAX_ERROR_SECONDS,
)

logger = logging.getLogger(__name__)

#: spec 8.4's ceiling: one run per repo, two across every repo in one daemon
DEFAULT_PER_REPO = 1
DEFAULT_GLOBAL = 2
MINUTE = 60.0


class LoopState(StrEnum):
    """Spec 8.3's state machine, as the page's badge and the statusline segment read it."""

    BUILDING = "building"
    OBSERVING = "observing"
    RUNNING = "running"
    PAUSED_BUDGET = "paused:budget"
    PAUSED_RATELIMIT = "paused:ratelimit"
    PAUSED_AUTH = "paused:auth"
    PAUSED_ERROR = "paused:error"
    DETACHED = "detached"


class Pause(BaseModel):
    """Why the loop stopped opening runs, and when it may start again (spec 8.4)."""

    model_config = ConfigDict(frozen=True)

    state: LoopState
    resumes_at: float | None = None


#: the words `sdk_runner.ASSISTANT_ERRORS` writes into a stopped run's ``error`` column
_PAUSE_WORDS: dict[str, LoopState] = {
    "paused:auth": LoopState.PAUSED_AUTH,
    "paused:ratelimit": LoopState.PAUSED_RATELIMIT,
    "paused:billing": LoopState.PAUSED_BUDGET,
}


def _resets_at(error: str) -> float | None:
    """The epoch in ``paused:ratelimit until <epoch>``, or ``None`` when the SDK named none."""
    _, _, tail = error.partition(" until ")
    try:
        return float(tail)
    except ValueError:
        return None


def pause_of(
    error: str | None,
    *,
    now: float,
    minutes: float = DEFAULT_RATELIMIT_MINUTES,
    auth_minutes: float = DEFAULT_AUTH_MINUTES,
) -> Pause | None:
    """The pause a stopped run's ``error`` asks for, or ``None`` when it asks for none.

    The runner writes a sentence because a run row has one error column; the loop wants the
    deadline, so the epoch is read back off it and anything unreadable falls back to a window.
    """
    if not error:
        return None
    state = _PAUSE_WORDS.get(error.split(" ", 1)[0])
    if state is None:
        return None
    if state is LoopState.PAUSED_AUTH:
        return Pause(state=state, resumes_at=now + auth_minutes * MINUTE)
    if state is not LoopState.PAUSED_RATELIMIT:
        return Pause(state=state)
    return Pause(state=state, resumes_at=_resets_at(error) or now + minutes * MINUTE)


class Backoff:
    """A doubling wait with a ceiling, reset by whatever counts as success.

    One implementation for the two places a repeated failure has to stop costing: a loop whose
    pass raised, and a repo whose loop will not build at all.
    """

    def __init__(
        self,
        *,
        first: float = DEFAULT_ERROR_SECONDS,
        ceiling: float = MAX_ERROR_SECONDS,
    ) -> None:
        self.first = first
        self.ceiling = ceiling
        self.failures = 0
        self.until: float | None = None

    def failed(self, *, now: float) -> float:
        """Record one more failure and answer the seconds to wait before trying again."""
        self.failures += 1
        wait = min(self.first * 2.0 ** (self.failures - 1), self.ceiling)
        self.until = now + wait
        return wait

    def ready(self, *, now: float) -> bool:
        """Whether the next attempt may happen yet."""
        return self.until is None or now >= self.until

    def cleared(self) -> None:
        """Success: the next failure starts the doubling over."""
        self.failures = 0
        self.until = None


class Pauses:
    """The pauses in force for one repo. Nothing here is persisted (recon Q4).

    A budget pause is recomputed from `spend_since` on every tick, so only the two the runner
    reports need holding, and both hold a deadline: nothing else could ever clear them, because
    nothing runs while they are in force.
    """

    def __init__(self, *, errors: Backoff | None = None) -> None:
        self.resumes_at: float | None = None
        self.auth: str = ""
        self.auth_until: float | None = None
        self.error: str = ""
        self.errors = errors or Backoff()

    def apply(self, pause: Pause | None) -> None:
        """Take on what a stopped run reported, if anything."""
        if pause is None:
            return
        if pause.state is LoopState.PAUSED_AUTH:
            self.auth = "the runner could not authenticate"
            self.auth_until = pause.resumes_at
        elif pause.state is LoopState.PAUSED_RATELIMIT:
            self.resumes_at = pause.resumes_at

    def authenticated(self) -> None:
        """A run that reported no auth refusal proves the credentials work: drop the hold."""
        self.auth = ""
        self.auth_until = None

    def failed(self, reason: str, *, now: float) -> float:
        """Hold this repo after a pass raised, and answer the seconds its driver should wait."""
        self.error = reason
        return self.errors.failed(now=now)

    def recovered(self) -> None:
        """A pass that finished: drop the error hold so the next failure waits the first window."""
        self.error = ""
        self.errors.cleared()

    def cleared(self) -> None:
        """Forget every held pause: an attach re-asks the runner and the budget re-reads itself."""
        self.resumes_at = None
        self.authenticated()
        self.recovered()

    def state(self, *, budget: BudgetState, now: float) -> LoopState | None:
        """The pause in force, or ``None`` when the loop may open a run.

        The error hold first: a loop whose last pass raised has no answer about anything else.
        Auth next: a loop that cannot authenticate would otherwise report a budget it can never
        spend. Every held pause clears itself the moment its own deadline passes.
        """
        if self.error:
            if self.errors.ready(now=now):
                # the backoff expired: the next pass is the retry, and `recovered` settles it
                self.error = ""
            else:
                return LoopState.PAUSED_ERROR
        if self.auth:
            if self.auth_until is None or now < self.auth_until:
                return LoopState.PAUSED_AUTH
            # the hold expired: the next run is the probe, and `authenticated` settles it
            self.authenticated()
        if self.resumes_at is not None:
            if now < self.resumes_at:
                return LoopState.PAUSED_RATELIMIT
            self.resumes_at = None
        return LoopState.PAUSED_BUDGET if budget.exhausted else None


class EventFeed(ABC):
    """Where a `RepoLoop` takes its edit events: the daemon's drain and a test harness."""

    @abstractmethod
    async def take(self, timeout: float) -> tuple[Event, ...]:
        """Every event waiting now, or ``()`` when ``timeout`` passed with none."""


class QueueFeed(EventFeed):
    """An `asyncio.Queue` the daemon's drain thread hands batches to (spec 8.1).

    The loop is bound on first use from the thread that runs it, so constructing this off a loop
    is safe and an `offer` that arrives before the first `take` is held rather than dropped. It is
    bounded both sides, so a loop that stopped taking cannot grow the daemon without end (H2).
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        cap: int = FEED_EVENT_CAP,
    ) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._loop = loop
        self.cap = cap
        self._early: deque[Event] = deque(maxlen=cap)

    def offer(self, events: Iterable[Event]) -> None:
        """Hand a drained spool over, from the daemon's thread or from the loop's own."""
        loop = self._loop
        for event in events:
            if loop is None:
                self._early.append(
                    event
                )  # a bounded deque drops the oldest, atomically
            else:
                loop.call_soon_threadsafe(self._put, event)

    def _put(self, event: Event) -> None:
        """Take one event onto the loop's own thread, dropping the oldest once the cap is full."""
        while self._queue.qsize() >= self.cap:
            self._queue.get_nowait()
        self._queue.put_nowait(event)

    async def take(self, timeout: float) -> tuple[Event, ...]:
        self._loop = self._loop or asyncio.get_running_loop()
        while self._early:
            self._queue.put_nowait(self._early.popleft())
        try:
            first = await asyncio.wait_for(self._queue.get(), timeout)
        except TimeoutError:
            return ()
        out = [first]
        while not self._queue.empty():
            out.append(self._queue.get_nowait())
        return tuple(out)


async def debounced(
    feed: EventFeed,
    *,
    seconds: float,
    timeout: float,
    restarts: float = DEBOUNCE_WINDOW_CAP,
    max_seconds: float | None = None,
) -> tuple[Event, ...]:
    """One batch per quiet window (spec 8.3 item 2): the window restarts on every event.

    Last event wins, so a burst of `PostToolUse` posts and the `Stop` path set behind them are one
    batch. The restarts are bounded, so an edit stream faster than the window still yields.
    """
    batch = await feed.take(timeout)
    if not batch:
        return ()
    collected = list(batch)
    remaining = seconds * restarts if max_seconds is None else max_seconds
    while seconds > 0 and remaining > 0:
        more = await feed.take(min(seconds, remaining))
        if not more:
            break
        collected.extend(more)
        remaining -= seconds
    return tuple(collected)


class Retries:
    """Spec 8.5's "targets re-queued once": a loop-side budget, not a database write (recon Q11).

    `graph_unresolved` is replaced by every build, so an aborted run loses no row; what it loses
    is the loop's intent, and the spec grants that one more attempt.
    """

    def __init__(self, *, retries: int = 1) -> None:
        self.retries = retries
        self._spent: dict[NodePair, int] = {}

    def aborted(self, pairs: Iterable[NodePair]) -> None:
        for pair in pairs:
            self._spent[pair] = self._spent.get(pair, 0) + 1

    def allowed(self, pair: NodePair) -> bool:
        return self._spent.get(pair, 0) <= self.retries

    def keep(self, pairs: Iterable[NodePair]) -> tuple[NodePair, ...]:
        return tuple(pair for pair in pairs if self.allowed(pair))


class RunGuard(BaseModel):
    """Spec 8.5's pre-run read: what a run is pinned to, and whether the tree was dirty."""

    model_config = ConfigDict(frozen=True)

    checkout: Checkout = Checkout()
    dirty: bool = False


async def read_guard(
    root: Path, *, status: Callable[[Path], tuple[str, ...] | None] = git_status_paths
) -> RunGuard:
    """Branch, HEAD and dirtiness, all off the event loop (spec 8.5's 2 to 46 ms).

    ``dirty`` counts only paths this auditor would audit, which is what ignoring ``.auditor/`` and
    the discovery excludes amounts to: an uncommitted README cannot invalidate an anchor.
    """
    head, changed = await asyncio.gather(
        git_head(root), asyncio.to_thread(status, root)
    )
    branch, commit = head
    finder = FileDiscovery(root)
    dirty = any(finder.auditable_shape(path) for path in changed or ())
    return RunGuard(checkout=Checkout(branch=branch, commit_sha=commit), dirty=dirty)


class RunSlots:
    """Spec 8.4's "one run per repo, two globally", owned by the daemon and shared by every loop.

    "Globally" is across `RepoLoop`s in one daemon, so the instance has to be the daemon's rather
    than a loop's. An over quota caller waits, which is what a cap means; `RunRegistry.max_open`
    evicts instead and is deliberately not reused.
    """

    def __init__(
        self, *, per_repo: int = DEFAULT_PER_REPO, global_: int = DEFAULT_GLOBAL
    ) -> None:
        self.per_repo = per_repo
        self.global_ = global_
        self._everywhere = asyncio.Semaphore(global_)
        self._held: dict[str, int] = {}
        self._room = asyncio.Condition()

    def held(self, key: str) -> int:
        """How many runs this repo is holding right now."""
        return self._held.get(key, 0)

    async def acquire(self, key: str) -> None:
        """Wait until this repo and the daemon both have room, then take one slot of each."""
        async with self._room:
            await self._room.wait_for(lambda: self.held(key) < self.per_repo)
            self._held[key] = self.held(key) + 1
        try:
            await self._everywhere.acquire()
        except BaseException:
            await self._give_back(key)
            raise

    async def release(self, key: str) -> None:
        """Give back one slot of each. Releasing a key that holds none is a no-op."""
        if not self.held(key):
            return
        self._everywhere.release()
        await self._give_back(key)

    async def _give_back(self, key: str) -> None:
        async with self._room:
            remaining = self.held(key) - 1
            if remaining > 0:
                self._held[key] = remaining
            else:
                self._held.pop(key, None)
            self._room.notify_all()

    @asynccontextmanager
    async def slot(self, key: str) -> AsyncIterator[None]:
        """One run's slot, given back however the run ends."""
        await self.acquire(key)
        try:
            yield
        finally:
            await self.release(key)
