"""Relational integrity of the shared index: the ``repos`` parent table, the foreign keys from
each working table to it, enforcement (no orphan rows), and ON DELETE CASCADE when a repo is
forgotten. The store's worker connection runs with ``PRAGMA foreign_keys=ON``; these assert the
behaviour that depends on it, and introspect the on-disk schema directly."""

import asyncio
import sqlite3
import threading

import pytest

from auditor.database import IndexStore
from auditor.database.base import SCHEMA_VERSION, Column, UnmigratableColumn
from auditor.database.files import FilesDB
from auditor.database.ignores import IgnoresDB
from auditor.models import (
    Category,
    FileRole,
    Finding,
    IndexEntry,
    Severity,
    VerdictKind,
)
from auditor.partition import Partition

_WORKING_TABLES = ["files", "file_rules", "findings", "shapes"]


def _finding(rule_id: str = "PY-X", line: int = 1) -> Finding:
    return Finding(
        rule_id=rule_id,
        category=Category.SECURITY,
        severity=Severity.HIGH,
        verdict_kind=VerdictKind.AUTO,
        line=line,
        message="m",
    )


def _entry(path: str = "x.py") -> IndexEntry:
    return IndexEntry(
        path=path,
        sha256="abc",
        lines=3,
        language="python",
        role=FileRole.PRODUCTION,
        last_scanned=1.0,
    )


async def _populate(index: IndexStore) -> None:
    """Write one row into each working table for the index's bound repo."""
    await index.files.upsert(_entry("x.py"))
    await index.findings.record("x.py", "PY-X", "fp", [_finding("PY-X")], 1.0)
    await index.shapes.add([("hash1", "func", "x.py", "f", 1)])


def _raw(db) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


# --- the repos parent table -----------------------------------------------------------------


async def test_bare_connect_does_not_register(tmp_path):
    # a read-only / cross-repo connection must not leave a placeholder repo behind
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/alpha") as a:
        assert await a.repos.list() == []


async def test_write_registers_repo(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/alpha") as a:
        await a.findings.add("x.py", [_finding()])  # any write registers the repo
        regs = await a.repos.list()
    assert [r["repo"] for r in regs] == ["/repos/alpha"]
    assert regs[0]["name"] == "alpha"  # path basename
    assert regs[0]["last_scanned"] == 0  # not stamped until a scan calls register()


async def test_register_refreshes_name_and_time(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/x/proj") as s:
        await s.repos.register(
            123.5
        )  # name is derived from the repo key's basename, not passed in
        regs = await s.repos.list()
    assert regs == [{"repo": "/x/proj", "name": "proj", "last_scanned": 123.5}]


# --- foreign keys + relationships -----------------------------------------------------------


@pytest.mark.parametrize("table", _WORKING_TABLES)
async def test_each_working_table_fks_to_repos(tmp_path, table):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r"):
        pass
    conn = _raw(db)
    fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    conn.close()
    # exactly one FK: <table>.repo -> repos.repo, cascading on delete
    assert len(fks) == 1
    fk = fks[0]
    assert (fk["table"], fk["from"], fk["to"], fk["on_delete"]) == (
        "repos",
        "repo",
        "repo",
        "CASCADE",
    )


# a fully-valid row per table EXCEPT the repo references an unregistered parent — so the only
# constraint that can fire is the foreign key (not a NOT NULL on some other column)
_ORPHAN_ROWS = {
    "files": (
        "(repo, path, sha256, lines, language, role, last_scanned)",
        ("/ghost", "x.py", "h", 1, "python", "production", 0),
    ),
    "file_rules": (
        "(repo, path, rule_id, fingerprint, last_scanned)",
        ("/ghost", "x.py", "R", "fp", 0),
    ),
    "findings": (
        "(repo, path, rule_id, category, severity, verdict_kind, line, message)",
        ("/ghost", "x.py", "R", "security", "high", "auto", 1, "m"),
    ),
    "shapes": (
        "(repo, shape_hash, kind, path, symbol, line)",
        ("/ghost", "h", "model", "x.py", "S", 1),
    ),
}


@pytest.mark.parametrize("table", _WORKING_TABLES)
async def test_foreign_keys_enforced_no_orphans(tmp_path, table):
    """An unregistered repo can't get a working-table row — the relationship is enforced. The row
    is otherwise valid, so an IntegrityError here is specifically the foreign key firing."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r"):
        pass
    cols, values = _ORPHAN_ROWS[table]
    placeholders = ", ".join("?" * len(values))
    conn = _raw(db)
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO {table} {cols} VALUES ({placeholders})", values)  # noqa: S608
    conn.close()


async def test_forget_cascades_to_every_table(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/a") as a:
        await _populate(a)
    async with await IndexStore.connect(db, "/repos/b") as b:
        await _populate(b)
        assert (
            await b.repos.forget("/repos/a") is True
        )  # delete the parent row → cascade

    conn = _raw(db)
    assert {r["repo"] for r in conn.execute("SELECT repo FROM repos")} == {"/repos/b"}
    for table in _WORKING_TABLES:
        present = {
            r["repo"] for r in conn.execute(f"SELECT DISTINCT repo FROM {table}")
        }  # noqa: S608
        assert present == {"/repos/b"}, (
            f"{table} kept orphan rows for the forgotten repo"
        )
    conn.close()


async def test_forget_unknown_repo_returns_false(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        assert await s.repos.forget("/nope") is False


async def test_forget_defaults_to_own_repo(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/solo") as s:
        await _populate(s)
        assert await s.repos.forget() is True  # no arg → forgets this handle's own repo
        assert await s.repos.list() == []
        assert await s.findings.all() == []  # cascade cleared its data


async def test_schema_version_recorded(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r"):
        pass
    conn = _raw(db)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# New characterisation / coverage tests
# ---------------------------------------------------------------------------


async def test_findings_grouped_by_path(tmp_path):
    """findings_grouped returns a dict keyed by path, each value being that file's findings."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as store:
        await store.files.upsert(_entry("a.py"))
        await store.findings.record(
            "a.py",
            "PY-X",
            "fp1",
            [_finding("PY-X", line=1), _finding("PY-X", line=2)],
            1.0,
        )
        await store.files.upsert(_entry("b.py"))
        await store.findings.record(
            "b.py", "PY-Y", "fp2", [_finding("PY-Y", line=5)], 1.0
        )

        grouped = await store.findings.grouped()

    assert set(grouped.keys()) == {"a.py", "b.py"}
    assert len(grouped["a.py"]) == 2
    assert len(grouped["b.py"]) == 1
    assert all(f.rule_id == "PY-X" for f in grouped["a.py"])
    assert grouped["b.py"][0].rule_id == "PY-Y"


async def test_findings_grouped_empty(tmp_path):
    """findings_grouped returns an empty dict when the store has no findings."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as store:
        grouped = await store.findings.grouped()
    assert grouped == {}


async def test_connect_defaults_the_identity_to_the_repo_key(tmp_path):
    async with await IndexStore.connect(tmp_path / "index.db", "/repos/alpha") as store:
        assert store.partition == Partition(identity="/repos/alpha", prefix="")
        assert store.findings.partition == store.partition  # every sub-store shares it


async def test_connect_binds_an_explicit_partition_to_every_store(tmp_path):
    part = Partition(identity="/checkout/.git", prefix="apps/backend/")
    async with await IndexStore.connect(
        tmp_path / "index.db", "/checkout/apps/backend", part
    ) as store:
        assert store.graph.partition == part
        assert store.repos.partition.prefix == "apps/backend/"
        assert store.repo == "/checkout/apps/backend"  # the partition key is unchanged


async def test_a_missing_identity_column_is_added_not_dropped(tmp_path):
    """The reconcile pass is what lets an identity table gain a column across a version bump."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        await s.ignores.add("PY-X", "a.py", 5, "ev", "keep me", 1.0)

    raw = _raw(db)
    raw.execute("ALTER TABLE ignores DROP COLUMN reason")  # simulate an older layout
    raw.execute("PRAGMA user_version=1")
    raw.commit()
    raw.close()

    async with await IndexStore.connect(db, "/r") as s:
        rows = await s.ignores.list()
    assert len(rows) == 1  # the row survived
    assert rows[0]["reason"] is None  # the re-added column is NULL, not missing


async def test_the_migrator_leaves_cache_tables_to_the_drop_sweep(tmp_path):
    """A cache table with a stale layout is dropped and recreated, never ALTERed."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        await s.findings.add("x.py", [_finding()])

    raw = _raw(db)
    raw.execute("ALTER TABLE findings DROP COLUMN message")
    raw.execute("PRAGMA user_version=1")
    raw.commit()
    raw.close()

    async with await IndexStore.connect(db, "/r") as s:
        assert await s.findings.all() == []  # dropped and rebuilt, not migrated
    raw = _raw(db)
    cols = {r["name"] for r in raw.execute("PRAGMA table_info(findings)")}
    raw.close()
    assert "message" in cols


def _declare(monkeypatch, extra: Column) -> None:
    """Append one column to the `ignores` declaration for the length of a test."""
    table = IgnoresDB.TABLES["ignores"]
    monkeypatch.setitem(
        IgnoresDB.TABLES,
        "ignores",
        table.model_copy(update={"cols": (*table.cols, extra)}),
    )


@pytest.mark.parametrize(
    "bad, why",
    [
        (Column(name="added", type="TEXT", not_null=True), "NOT NULL"),
        (Column(name="added", type="TEXT", references="repos (repo)"), "REFERENCES"),
        (Column(name="added", type="TEXT", primary_key=True), "PRIMARY KEY"),
    ],
    ids=["not_null_without_default", "references", "primary_key"],
)
async def test_a_column_sqlite_cannot_add_names_itself(tmp_path, monkeypatch, bad, why):
    """SQLite rejects all three shapes on an existing table. The migrator runs on every connect
    for every repo, so it has to fail with the table and column rather than a bare
    OperationalError — or, for the primary key, silently skip the column."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r"):
        pass
    _declare(monkeypatch, bad)
    with pytest.raises(UnmigratableColumn, match="ignores.added") as excinfo:
        await IndexStore.connect(db, "/r")
    assert why in str(excinfo.value)
    assert (excinfo.value.table, excinfo.value.column) == ("ignores", "added")


async def test_a_not_null_column_with_a_default_migrates(tmp_path, monkeypatch):
    """The rule the migrator's docstring states: NOT NULL is only unmigratable without a
    default, so this shape has to keep working."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as store:
        await store.ignores.add("PY-X", "a.py", 5, "ev", "keep me", 1.0)
    _declare(
        monkeypatch, Column(name="added", type="TEXT", not_null=True, default="'x'")
    )
    async with await IndexStore.connect(db, "/r") as store:
        assert len(await store.ignores.list()) == 1  # the row survived the ALTER
    raw = _raw(db)
    added = [r["added"] for r in raw.execute("SELECT added FROM ignores")]
    raw.close()
    assert added == ["x"]  # the default filled the existing row


async def test_a_failed_migration_leaves_the_version_and_the_cache_intact(
    tmp_path, monkeypatch
):
    """The bump is one transaction and the reconcile runs first, so a declaration that cannot
    land never costs a user their cached findings or their stamp."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as store:
        await store.findings.add("x.py", [_finding()])
    _declare(monkeypatch, Column(name="added", type="TEXT", not_null=True))
    raw = _raw(db)
    raw.execute("PRAGMA user_version=1")  # force the drop sweep to be reachable
    raw.commit()
    raw.close()

    with pytest.raises(UnmigratableColumn):
        await IndexStore.connect(db, "/r")

    raw = _raw(db)
    version = raw.execute("PRAGMA user_version").fetchone()[0]
    findings = raw.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]
    raw.close()
    assert (version, findings) == (1, 1)


async def test_a_failed_bump_leaves_no_worker_thread_behind(tmp_path, monkeypatch):
    """`connect` owns the worker until it hands the store back, so a raising `_init_schema` has
    to close it rather than leak a thread per invocation."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r"):
        pass
    _declare(monkeypatch, Column(name="added", type="TEXT", not_null=True))
    before = threading.active_count()
    with pytest.raises(UnmigratableColumn):
        await IndexStore.connect(db, "/r")
    for _ in range(200):
        if threading.active_count() <= before:
            break
        await asyncio.sleep(0.01)
    assert threading.active_count() <= before


async def test_a_version_zero_index_with_rows_is_rebuilt(tmp_path):
    """A stamp of 0 on a populated database is a lost stamp, not a fresh database: the cache
    tables have to be dropped and rebuilt, or every later write hits the old layout."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as store:
        await store.findings.add("x.py", [_finding()])
        await store.ignores.add("PY-X", "a.py", 5, "ev", "keep me", 1.0)

    raw = _raw(db)
    raw.execute("ALTER TABLE findings DROP COLUMN message")  # a stale cache layout
    raw.execute("PRAGMA user_version=0")
    raw.commit()
    raw.close()

    async with await IndexStore.connect(db, "/r") as store:
        assert await store.findings.all() == []  # dropped and rebuilt
        await store.findings.add("x.py", [_finding()])  # the new layout takes writes
        assert len(await store.findings.all()) == 1
        assert len(await store.ignores.list()) == 1  # user state survived
    raw = _raw(db)
    version = raw.execute("PRAGMA user_version").fetchone()[0]
    raw.close()
    assert version == SCHEMA_VERSION


async def test_a_second_connection_never_sees_a_half_bumped_schema(tmp_path):
    """The whole bump is one IMMEDIATE transaction, so a reader either sees the old schema or
    the new one, never a moment with the cache tables dropped and not yet recreated."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as store:
        await store.findings.add("x.py", [_finding()])
    raw = _raw(db)
    raw.execute("PRAGMA user_version=1")
    raw.commit()
    raw.close()

    seen: list[int] = []
    stop = threading.Event()

    def poll() -> None:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        while not stop.is_set():
            seen.append(conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0])
        conn.close()

    reader = threading.Thread(target=poll)
    reader.start()
    try:
        async with await IndexStore.connect(db, "/r"):
            pass
    finally:
        stop.set()
        reader.join()
    assert set(seen) <= {0, 1}  # never a `no such table`, never a torn count


def test_column_names_include_the_repo_foreign_key():
    """`files` and `ignores` are the two shapes: repo_fk prepends the FK, repo_fk=False does not.
    Neither table is touched by this slice, so this assertion cannot go stale under it."""
    names = FilesDB.TABLES["files"].column_names()
    assert names[:3] == ("repo", "path", "sha256")
    assert IgnoresDB.TABLES["ignores"].column_names()[0] == "id"
