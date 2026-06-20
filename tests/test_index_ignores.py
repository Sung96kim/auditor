"""IndexStore ignore CRUD: add (idempotent upsert), list, remove by id + selector, clear,
repo-partitioning, FK cascade on forget, and survival across a schema-version rebuild."""

import sqlite3

from auditor.database import IndexStore
from auditor.database.base import _CACHE_TABLES, _SCHEMA_VERSION


async def test_add_list_roundtrip(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        rid = await s.ignores.add_ignore("PY-X", "a.py", 5, "ev", "why", 1.0)
        rows = await s.ignores.list()
    assert rid == 1
    assert rows == [
        {
            "id": 1,
            "rule_id": "PY-X",
            "file": "a.py",
            "line": 5,
            "evidence_hash": "ev",
            "reason": "why",
            "created_at": 1.0,
        }
    ]


async def test_add_is_idempotent_per_scope(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        first = await s.ignores.add_ignore("PY-X", "a.py", 5, "ev1", "r1", 1.0)
        second = await s.ignores.add_ignore(
            "PY-X", "a.py", 5, "ev2", "r2", 2.0
        )  # same scope
        rows = await s.ignores.list()
    assert first == second  # same row id reused
    assert len(rows) == 1
    assert rows[0]["evidence_hash"] == "ev2" and rows[0]["reason"] == "r2"  # refreshed


async def test_distinct_scopes_are_separate_rows(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        await s.ignores.add_ignore("PY-X", None, None, None, None, 1.0)  # repo-wide
        await s.ignores.add_ignore("PY-X", "a.py", None, None, None, 1.0)  # file-wide
        await s.ignores.add_ignore("PY-X", "a.py", 5, "ev", None, 1.0)  # line-level
        rows = await s.ignores.list()
    assert len(rows) == 3


async def test_remove_by_id_and_by_selector(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        i1 = await s.ignores.add_ignore("PY-A", "a.py", None, None, None, 1.0)
        await s.ignores.add_ignore("PY-B", None, None, None, None, 1.0)
        assert await s.ignores.remove_ignore_by_id(i1) is True
        assert await s.ignores.remove_ignore_by_id(9999) is False
        assert await s.ignores.remove_ignore_by_selector("PY-B", None, None) is True
        assert await s.ignores.remove_ignore_by_selector("PY-B", None, None) is False
        assert await s.ignores.list() == []


async def test_clear(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        await s.ignores.add_ignore("PY-A", None, None, None, None, 1.0)
        await s.ignores.add_ignore("PY-B", "a.py", None, None, None, 1.0)
        assert await s.ignores.clear_ignores() == 2
        assert await s.ignores.list() == []


async def test_ignores_are_repo_partitioned(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/a") as a:
        await a.ignores.add_ignore("PY-X", None, None, None, None, 1.0)
    async with await IndexStore.connect(db, "/repos/b") as b:
        await b.ignores.add_ignore("PY-Y", None, None, None, None, 1.0)
        assert [r["rule_id"] for r in await b.ignores.list()] == ["PY-Y"]
    async with await IndexStore.connect(db, "/repos/a") as a:
        assert [r["rule_id"] for r in await a.ignores.list()] == ["PY-X"]


async def test_forget_cascades_to_ignores(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/a") as a:
        await a.ignores.add_ignore("PY-X", None, None, None, None, 1.0)
        await a.repos.forget()  # delete the repo → FK cascade
        assert await a.ignores.list() == []
    raw = sqlite3.connect(db)
    assert raw.execute("SELECT COUNT(*) FROM ignores").fetchone()[0] == 0
    raw.close()


async def test_ignores_survive_schema_version_rebuild(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        await s.ignores.add_ignore("PY-X", "a.py", 5, "ev", "keep me", 1.0)

    # force a stale schema version on disk → reconnect triggers the cache rebuild
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA user_version=1")
    raw.commit()
    raw.close()

    async with await IndexStore.connect(db, "/r") as s:
        rows = await s.ignores.list()
    assert (
        len(rows) == 1 and rows[0]["reason"] == "keep me"
    )  # not dropped by the rebuild


async def test_ignores_table_fks_to_repos_with_cascade(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r"):
        pass
    raw = sqlite3.connect(db)
    fks = raw.execute("PRAGMA foreign_key_list(ignores)").fetchall()
    raw.close()
    assert len(fks) == 1
    # (id, seq, table, from, to, on_update, on_delete, match)
    assert (fks[0][2], fks[0][3], fks[0][4], fks[0][6]) == (
        "repos",
        "repo",
        "repo",
        "CASCADE",
    )


def test_cache_tables_exclude_ignores_and_repos():
    # the rebuild-on-bump must never drop user-authored state
    assert "ignores" not in _CACHE_TABLES
    assert "repos" not in _CACHE_TABLES
    assert _SCHEMA_VERSION >= 4
