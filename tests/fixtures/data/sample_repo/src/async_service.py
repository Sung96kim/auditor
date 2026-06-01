"""Async ingestion service. Exercises async detectors plus the nested-scope edge cases
that must NOT fire (sync I/O inside a nested *sync* helper, properly-stored tasks)."""

import asyncio

import requests


class Ingestor:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    async def poll_blocking(self, url: str) -> bytes:
        # PY-SEC-* aside, this is PY-ASYNC-SYNC-IO (sync requests in async)
        resp = requests.get(url, timeout=5)
        return resp.content

    async def sleep_wrong(self) -> None:
        # PY-ASYNC-SYNC-IO (time.sleep blocks the loop)
        import time as _time  # also PY-STYLE-INLINE-IMPORT

        _time.sleep(0.1)

    async def fire_and_forget(self, coro_fn) -> None:
        # PY-ASYNC-DANGLING-TASK (result discarded)
        asyncio.create_task(coro_fn())

    async def tracked(self, coro_fn) -> None:
        # negative: task stored -> must NOT trip dangling-task
        task = asyncio.create_task(coro_fn())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def fan_out_sequential(self, urls: list[str]) -> list[bytes]:
        # PY-ASYNC-SEQUENTIAL-AWAITS (independent awaits in a loop)
        out = []
        for url in urls:
            out.append(await self._fetch(url))
        return out

    async def fan_out_concurrent(self, urls: list[str]) -> list[bytes]:
        # negative: gather, not a per-iteration await
        return await asyncio.gather(*[self._fetch(url) for url in urls])

    async def _fetch(self, url: str) -> bytes:
        async with asyncio.timeout(10):
            return await _read_remote(url)

    async def ping(self) -> str:
        # PY-ASYNC-NO-AWAIT-BODY (async def with no await)
        return "pong"

    async def with_nested_sync(self, path: str) -> int:
        # edge: open() is in a NESTED SYNC function, NOT directly in this async body,
        # so PY-ASYNC-SYNC-IO must NOT fire here.
        def _count_lines() -> int:
            with open(path) as fh:
                return sum(1 for _ in fh)

        return await asyncio.to_thread(_count_lines)


async def _read_remote(url: str) -> bytes:
    await asyncio.sleep(0)
    return b""
