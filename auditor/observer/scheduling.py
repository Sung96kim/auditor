"""How many refinement runs the daemon lets happen at once (spec 8.4).

S8c seam 6: S8b constructs one `RunSlots` per daemon and opens no run, so nothing takes a slot
until the repo loop lands.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

#: spec 8.4's ceiling: one run per repo, two across every repo in one daemon
DEFAULT_PER_REPO = 1
DEFAULT_GLOBAL = 2


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
