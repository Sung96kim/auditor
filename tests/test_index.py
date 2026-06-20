"""index.py: IndexStore scope, per-rule cache, findings, shapes, and the async SQLite
worker under concurrency + high load (direct unit tests)."""

import asyncio
import time

import pytest

from auditor.index import IndexStore
from auditor.models import (
    Category,
    FileRole,
    Finding,
    IndexEntry,
    Severity,
    VerdictKind,
)


def _finding(rule="PY-SEC-DANGEROUS-EVAL", sev=Severity.BLOCKING) -> Finding:
    return Finding(
        rule_id=rule,
        category=Category.SECURITY,
        severity=sev,
        verdict_kind=VerdictKind.AUTO,
        line=2,
        message="eval",
    )


def _entry(path: str, sha: str = "h") -> IndexEntry:
    return IndexEntry(
        path=path,
        sha256=sha,
        lines=1,
        language="python",
        role=FileRole.PRODUCTION,
        last_scanned=time.time(),
    )


async def test_scope_registry(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        await index.add_scope(["a.py", "b.py"])
        await index.add_scope(["a.py"])  # idempotent
        assert await index.scope() == ["a.py", "b.py"]


async def test_record_and_read_back(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.upsert_file(
            IndexEntry(
                path="a.py",
                sha256="h1",
                lines=3,
                language="python",
                role=FileRole.PRODUCTION,
                last_scanned=now,
            )
        )
        await index.record_rule(
            "a.py", "PY-SEC-DANGEROUS-EVAL", "fp1", [_finding()], now
        )
        assert await index.file_sha("a.py") == "h1"
        assert await index.rule_fingerprint("a.py", "PY-SEC-DANGEROUS-EVAL") == "fp1"
        cached = await index.cached_findings("a.py", "PY-SEC-DANGEROUS-EVAL")
        assert len(cached) == 1 and cached[0].rule_id == "PY-SEC-DANGEROUS-EVAL"
        files = await index.files.list()
        assert files[0].counts.get("blocking") == 1


async def test_record_rule_replaces(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.upsert_file(
            IndexEntry(
                path="a.py",
                sha256="h",
                lines=1,
                language="python",
                role=FileRole.PRODUCTION,
                last_scanned=now,
            )
        )
        await index.record_rule("a.py", "R", "fp", [_finding("R"), _finding("R")], now)
        await index.record_rule("a.py", "R", "fp", [], now)  # re-run, no findings
        assert await index.cached_findings("a.py", "R") == []
        assert (
            await index.rule_fingerprint("a.py", "R") == "fp"
        )  # ran-clean, not absent


async def test_shapes_and_duplicates(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        await index.add_shapes([("hh", "model", "a.py", "A", 1)])
        await index.add_shapes([("hh", "model", "b.py", "B", 1)])
        await index.add_shapes([("other", "model", "a.py", "C", 5)])
        dups = await index.duplicate_shapes()
        assert "hh" in dups and "other" not in dups
        assert {r["path"] for r in dups["hh"]} == {"a.py", "b.py"}
        await index.clear_shapes("a.py")
        assert await index.duplicate_shapes() == {}


async def test_clear_findings_for_rules(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        await index.add_findings("a.py", [_finding("PY-XFILE-DUP-MODEL")])
        await index.clear_findings_for_rules(["PY-XFILE-DUP-MODEL"])
        assert await index.all_findings() == []


# --- concurrency + high load on the async SQLite worker -----------------------


async def test_single_store_many_concurrent_writers(tmp_path):
    """One IndexStore (one worker thread) fed by 300 concurrent coroutines — every op
    must serialize through the queue and return the right result to its own awaiter."""
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()

        async def task(i: int) -> int:
            path = f"f{i}.py"
            await index.upsert_file(_entry(path, sha=f"h{i}"))
            await index.record_rule(path, "R", f"fp{i}", [_finding("R")], now)
            return len(await index.cached_findings(path, "R"))

        results = await asyncio.gather(*[task(i) for i in range(300)])
        assert all(n == 1 for n in results)
        assert len(await index.files.list()) == 300
        # results were not cross-wired between concurrent callers
        assert await index.rule_fingerprint("f137.py", "R") == "fp137"


async def test_interleaved_readers_and_writers(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.upsert_file(_entry("a.py"))
        await index.record_rule("a.py", "R", "fp", [_finding("R")], now)

        async def reader() -> int:
            return len(await index.cached_findings("a.py", "R"))

        async def writer() -> None:
            await index.add_findings("a.py", [_finding("X")])

        ops = [reader() for _ in range(80)] + [writer() for _ in range(80)]
        await asyncio.gather(*ops)  # no deadlock / no "database is locked"
        assert len(await index.all_findings()) == 1 + 80  # seeded R + 80 X


async def test_high_load_volume(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.upsert_file(_entry("big.py"))
        await asyncio.gather(
            *[
                index.record_rule("big.py", f"R{i}", f"fp{i}", [_finding(f"R{i}")], now)
                for i in range(800)
            ]
        )
        assert len(await index.all_findings()) == 800


async def test_many_stores_heavy_contention(tmp_path):
    """24 independent IndexStores (24 worker threads + connections) hammering one DB file —
    WAL + busy_timeout must let every write land with no lock errors."""
    db = tmp_path / "i.db"
    async with await IndexStore.connect(db):  # initialize schema once
        pass
    now = time.time()

    async def worker(i: int) -> None:
        async with await IndexStore.connect(db) as index:
            for j in range(10):
                path = f"f{i}_{j}.py"
                await index.upsert_file(_entry(path, sha=f"h{i}{j}"))
                await index.record_rule(path, "R", "fp", [_finding("R")], now)

    await asyncio.gather(*[worker(i) for i in range(24)])
    async with await IndexStore.connect(db) as index:
        assert len(await index.files.list()) == 24 * 10


async def test_worker_error_isolation_under_load(tmp_path):
    """A failing op surfaces to its own caller; the worker thread survives and concurrent
    good ops still complete."""
    async with await IndexStore.connect(tmp_path / "i.db") as index:

        async def bad() -> None:
            with pytest.raises(Exception):  # noqa: B017 - any DB error is fine
                await index._worker.run(
                    lambda c: c.execute("SELECT missing_col FROM files")
                )

        async def good(i: int) -> None:
            await index.add_shapes([(f"h{i}", "model", f"f{i}.py", "C", 1)])

        await asyncio.gather(bad(), *[good(i) for i in range(20)])
        # worker still alive and every good op landed
        await index.add_shapes(
            [("h0", "model", "other.py", "D", 1)]
        )  # collide with f0's hash
        assert "h0" in await index.duplicate_shapes()
