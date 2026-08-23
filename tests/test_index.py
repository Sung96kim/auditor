"""index.py: IndexStore scope, per-rule cache, findings, shapes, and the async SQLite
worker under concurrency + high load (direct unit tests)."""

import asyncio
import re
import time

import pytest

from auditor.database import IndexStore
from auditor.database.base import BaseDB
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
        await index.files.add_scope(["a.py", "b.py"])
        await index.files.add_scope(["a.py"])  # idempotent
        assert await index.files.scope() == ["a.py", "b.py"]


async def test_record_and_read_back(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.files.upsert(
            IndexEntry(
                path="a.py",
                sha256="h1",
                lines=3,
                language="python",
                role=FileRole.PRODUCTION,
                last_scanned=now,
            )
        )
        await index.findings.record(
            "a.py", "PY-SEC-DANGEROUS-EVAL", "fp1", [_finding()], now
        )
        assert await index.files.sha("a.py") == "h1"
        assert (
            await index.findings.fingerprint("a.py", "PY-SEC-DANGEROUS-EVAL") == "fp1"
        )
        cached = await index.findings.cached("a.py", "PY-SEC-DANGEROUS-EVAL")
        assert len(cached) == 1 and cached[0].rule_id == "PY-SEC-DANGEROUS-EVAL"
        files = await index.files.list()
        assert files[0].counts.get("blocking") == 1


async def test_record_rule_replaces(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.files.upsert(
            IndexEntry(
                path="a.py",
                sha256="h",
                lines=1,
                language="python",
                role=FileRole.PRODUCTION,
                last_scanned=now,
            )
        )
        await index.findings.record(
            "a.py", "R", "fp", [_finding("R"), _finding("R")], now
        )
        await index.findings.record("a.py", "R", "fp", [], now)  # re-run, no findings
        assert await index.findings.cached("a.py", "R") == []
        assert (
            await index.findings.fingerprint("a.py", "R") == "fp"
        )  # ran-clean, not absent


async def test_fingerprints_batched(tmp_path):
    """fingerprints(path) returns every rule's fingerprint in one call."""
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.files.upsert(_entry("a.py"))
        await index.findings.record("a.py", "R1", "fp1", [_finding("R1")], now)
        await index.findings.record("a.py", "R2", "fp2", [], now)
        assert await index.findings.fingerprints("a.py") == {"R1": "fp1", "R2": "fp2"}
        assert await index.findings.fingerprints("missing.py") == {}


async def test_cached_by_rule_batched(tmp_path):
    """cached_by_rule(path) groups a file's findings by rule in one call."""
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.files.upsert(_entry("a.py"))
        await index.findings.record(
            "a.py", "R1", "fp1", [_finding("R1"), _finding("R1")], now
        )
        await index.findings.record("a.py", "R2", "fp2", [_finding("R2")], now)
        by_rule = await index.findings.cached_by_rule("a.py")
        assert {k: len(v) for k, v in by_rule.items()} == {"R1": 2, "R2": 1}
        assert all(f.rule_id == "R1" for f in by_rule["R1"])
        assert await index.findings.cached_by_rule("missing.py") == {}


async def test_record_many_writes_and_replaces(tmp_path):
    """record_many writes several rules at once and replaces only the given rules."""
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.files.upsert(_entry("a.py"))
        await index.findings.record_many(
            "a.py",
            [("R1", "fp1", [_finding("R1")]), ("R2", "fp2", [_finding("R2")])],
            now,
        )
        assert await index.findings.fingerprints("a.py") == {"R1": "fp1", "R2": "fp2"}
        assert {
            k: len(v) for k, v in (await index.findings.cached_by_rule("a.py")).items()
        } == {
            "R1": 1,
            "R2": 1,
        }
        # Re-run R1 with no findings; R2 untouched.
        await index.findings.record_many("a.py", [("R1", "fp1b", [])], now)
        by_rule = await index.findings.cached_by_rule("a.py")
        assert "R1" not in by_rule and len(by_rule["R2"]) == 1
        assert (await index.findings.fingerprints("a.py")) == {
            "R1": "fp1b",
            "R2": "fp2",
        }


async def test_record_many_empty_noop(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        await index.files.upsert(_entry("a.py"))
        await index.findings.record_many("a.py", [], time.time())
        assert await index.findings.fingerprints("a.py") == {}


async def test_shapes_and_duplicates(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        await index.shapes.add([("hh", "model", "a.py", "A", 1)])
        await index.shapes.add([("hh", "model", "b.py", "B", 1)])
        await index.shapes.add([("other", "model", "a.py", "C", 5)])
        dups = await index.shapes.duplicates()
        assert "hh" in dups and "other" not in dups
        assert {r["path"] for r in dups["hh"]} == {"a.py", "b.py"}
        await index.shapes.clear("a.py")
        assert await index.shapes.duplicates() == {}


async def test_duplicates_multiple_groups(tmp_path):
    """The single-query duplicates() returns every multi-file hash, rows ordered by path/line."""
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        await index.shapes.add([("h1", "model", "b.py", "B", 9)])
        await index.shapes.add([("h1", "model", "a.py", "A", 1)])
        await index.shapes.add([("h2", "model", "a.py", "C", 1)])
        await index.shapes.add([("h2", "model", "c.py", "D", 1)])
        await index.shapes.add(
            [("solo", "model", "a.py", "E", 1)]
        )  # single file: excluded
        dups = await index.shapes.duplicates()
        assert set(dups) == {"h1", "h2"}
        # rows within a group ordered by (path, line)
        assert [r["path"] for r in dups["h1"]] == ["a.py", "b.py"]


async def test_clear_findings_for_rules(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        await index.findings.add("a.py", [_finding("PY-XFILE-DUP-MODEL")])
        await index.findings.clear_for_rules(["PY-XFILE-DUP-MODEL"])
        assert await index.findings.all() == []


async def test_by_rule_prefix(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        graph_finding = Finding(
            rule_id="GRAPH-COUPLING-HIGH",
            category=Category.SECURITY,
            severity=Severity.HIGH,
            verdict_kind=VerdictKind.AUTO,
            line=1,
            message="high coupling",
            evidence="m.py::Foo",
        )
        other_finding = Finding(
            rule_id="PY-STYLE-X",
            category=Category.SECURITY,
            severity=Severity.LOW,
            verdict_kind=VerdictKind.AUTO,
            line=1,
            message="style",
            evidence="m.py::Bar",
        )
        await index.findings.add("m.py", [graph_finding, other_finding])
        rows = await index.findings.by_rule_prefix("GRAPH-")
        assert len(rows) == 1
        assert rows[0]["rule_id"] == "GRAPH-COUPLING-HIGH"
        assert rows[0]["evidence"] == "m.py::Foo"
        assert await index.findings.by_rule_prefix("MISSING-") == []


# --- concurrency + high load on the async SQLite worker -----------------------


async def test_single_store_many_concurrent_writers(tmp_path):
    """One IndexStore (one worker thread) fed by 300 concurrent coroutines — every op
    must serialize through the queue and return the right result to its own awaiter."""
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()

        async def task(i: int) -> int:
            path = f"f{i}.py"
            await index.files.upsert(_entry(path, sha=f"h{i}"))
            await index.findings.record(path, "R", f"fp{i}", [_finding("R")], now)
            return len(await index.findings.cached(path, "R"))

        results = await asyncio.gather(*[task(i) for i in range(300)])
        assert all(n == 1 for n in results)
        assert len(await index.files.list()) == 300
        # results were not cross-wired between concurrent callers
        assert await index.findings.fingerprint("f137.py", "R") == "fp137"


async def test_interleaved_readers_and_writers(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.files.upsert(_entry("a.py"))
        await index.findings.record("a.py", "R", "fp", [_finding("R")], now)

        async def reader() -> int:
            return len(await index.findings.cached("a.py", "R"))

        async def writer() -> None:
            await index.findings.add("a.py", [_finding("X")])

        ops = [reader() for _ in range(80)] + [writer() for _ in range(80)]
        await asyncio.gather(*ops)  # no deadlock / no "database is locked"
        assert len(await index.findings.all()) == 1 + 80  # seeded R + 80 X


async def test_high_load_volume(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.files.upsert(_entry("big.py"))
        await asyncio.gather(
            *[
                index.findings.record(
                    "big.py", f"R{i}", f"fp{i}", [_finding(f"R{i}")], now
                )
                for i in range(800)
            ]
        )
        assert len(await index.findings.all()) == 800


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
                await index.files.upsert(_entry(path, sha=f"h{i}{j}"))
                await index.findings.record(path, "R", "fp", [_finding("R")], now)

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
            await index.shapes.add([(f"h{i}", "model", f"f{i}.py", "C", 1)])

        await asyncio.gather(bad(), *[good(i) for i in range(20)])
        # worker still alive and every good op landed
        await index.shapes.add(
            [("h0", "model", "other.py", "D", 1)]
        )  # collide with f0's hash
        assert "h0" in await index.shapes.duplicates()


async def test_transaction_commits_every_write_together(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db", "/r") as store:
        await store.transaction(
            lambda conn: (
                store.findings.write_add(conn, "a.py", [_finding("PY-A")]),
                store.findings.write_add(conn, "b.py", [_finding("PY-B")]),
            )
        )
        assert {f.rule_id for f in await store.findings.all()} == {"PY-A", "PY-B"}


async def test_transaction_rolls_back_on_failure(tmp_path):
    """A build that dies halfway must leave the previous state, not a half-written one."""

    def boom(conn):
        store.findings.write_add(conn, "a.py", [_finding("PY-A")])
        raise RuntimeError("detector exploded")

    async with await IndexStore.connect(tmp_path / "i.db", "/r") as store:
        with pytest.raises(RuntimeError, match="detector exploded"):
            await store.transaction(boom)
        assert await store.findings.all() == []


async def test_transaction_returns_what_the_callable_returns(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db", "/r") as store:
        assert await store.transaction(lambda conn: 7) == 7


def test_the_facade_exposes_every_registered_store():
    """The class docstring's list is the contract: a new store has to appear in the annotations
    and in the list a reader sees, so neither can drift from the registry."""
    registered = {store.attr for store in BaseDB._registry}
    listed = set(re.findall(r"``(\w+)``\s+—", IndexStore.__doc__))
    assert registered == set(IndexStore.__annotations__) == listed


async def test_a_finding_round_trips_its_subkind(tmp_path):
    """The column is what graph_overview reads, so a lost value silently empties both hub lists."""
    async with await IndexStore.connect(tmp_path / "index.db", "/repo") as index:
        finding = Finding(
            rule_id="GRAPH-GOD-CONCEPT",
            category=Category.OOP_COMPOSITION,
            severity=Severity.SUGGESTION,
            verdict_kind=VerdictKind.CANDIDATE,
            line=1,
            message="x has high fan-out (9)",
            evidence="m.py::x",
            subkind="fan_out",
        )
        await index.findings.add("m.py", [finding])
        assert (await index.findings.all())[0].subkind == "fan_out"
        rows = await index.findings.by_rule_prefix("GRAPH-")
        assert rows[0]["subkind"] == "fan_out"
