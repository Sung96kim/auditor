"""Comprehensive partitioning coverage: every IndexStore read/write is scoped to its bound
``repo``, so two repos sharing one db (and even identical relative paths / shape hashes) never
see or clobber each other's rows. One test per method/behaviour that gained the repo dimension."""

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

_A = "/repos/alpha"
_B = "/repos/beta"


def _finding(
    rule: str = "R", sev: Severity = Severity.BLOCKING, line: int = 1
) -> Finding:
    return Finding(
        rule_id=rule,
        category=Category.SECURITY,
        severity=sev,
        verdict_kind=VerdictKind.AUTO,
        line=line,
        message="m",
    )


def _entry(
    path: str = "x.py", sha: str = "h", role: FileRole = FileRole.PRODUCTION
) -> IndexEntry:
    return IndexEntry(
        path=path,
        sha256=sha,
        lines=1,
        language="python",
        role=role,
        last_scanned=time.time(),
    )


async def _store(db, repo: str) -> IndexStore:
    return await IndexStore.connect(db, repo)


# --- scope / files / cache are repo-scoped --------------------------------------------------


async def test_scope_is_per_repo(tmp_path):
    db = tmp_path / "index.db"
    async with await _store(db, _A) as a:
        await a.add_scope(["a.py", "shared.py"])
    async with await _store(db, _B) as b:
        await b.add_scope(["b.py", "shared.py"])
        assert await b.scope() == ["b.py", "shared.py"]
    async with await _store(db, _A) as a:
        assert await a.scope() == ["a.py", "shared.py"]  # unchanged by B


async def test_file_sha_and_fingerprint_are_per_repo(tmp_path):
    db = tmp_path / "index.db"
    now = time.time()
    async with await _store(db, _A) as a:
        await a.upsert_file(_entry("shared.py", sha="aaa"))
        await a.record_rule("shared.py", "R", "fp-a", [_finding()], now)
    async with await _store(db, _B) as b:
        await b.upsert_file(_entry("shared.py", sha="bbb"))
        await b.record_rule("shared.py", "R", "fp-b", [_finding()], now)
        assert await b.file_sha("shared.py") == "bbb"
        assert await b.rule_fingerprint("shared.py", "R") == "fp-b"
    async with await _store(db, _A) as a:
        assert await a.file_sha("shared.py") == "aaa"  # B's upsert didn't overwrite
        assert await a.rule_fingerprint("shared.py", "R") == "fp-a"


async def test_cached_and_all_findings_are_per_repo(tmp_path):
    db = tmp_path / "index.db"
    now = time.time()
    async with await _store(db, _A) as a:
        await a.upsert_file(_entry("shared.py"))
        # record_rule stores the rule's own findings; differentiate repos by line
        await a.record_rule(
            "shared.py", "R", "fp", [_finding("R", line=1), _finding("R", line=2)], now
        )
    async with await _store(db, _B) as b:
        await b.upsert_file(_entry("shared.py"))
        await b.record_rule("shared.py", "R", "fp", [_finding("R", line=9)], now)
        assert [f.line for f in await b.cached_findings("shared.py", "R")] == [9]
        assert [f.line for f in await b.all_findings()] == [9]
    async with await _store(db, _A) as a:
        assert sorted(f.line for f in await a.all_findings()) == [1, 2]


async def test_files_and_severity_counts_are_per_repo(tmp_path):
    db = tmp_path / "index.db"
    now = time.time()
    async with await _store(db, _A) as a:
        await a.upsert_file(_entry("shared.py"))
        await a.record_rule(
            "shared.py", "R", "fp", [_finding(sev=Severity.BLOCKING)], now
        )
    async with await _store(db, _B) as b:
        await b.upsert_file(_entry("shared.py"))
        await b.upsert_file(_entry("only_b.py"))
        await b.record_rule("shared.py", "R", "fp", [_finding(sev=Severity.LOW)], now)
        b_files = {e.path: e for e in await b.files()}
        assert set(b_files) == {"shared.py", "only_b.py"}
        assert b_files["shared.py"].counts.get("low") == 1
        assert b_files["shared.py"].counts.get("blocking") is None  # A's count not seen
    async with await _store(db, _A) as a:
        a_files = {e.path: e for e in await a.files()}
        assert set(a_files) == {"shared.py"}  # B's only_b.py not visible
        assert a_files["shared.py"].counts.get("blocking") == 1


# --- doc_path / roles / clear are repo-scoped -----------------------------------------------


async def test_set_doc_path_is_per_repo(tmp_path):
    db = tmp_path / "index.db"
    async with await _store(db, _A) as a:
        await a.upsert_file(_entry("shared.py"))
        await a.set_doc_path("shared.py", "docs/a.md")
    async with await _store(db, _B) as b:
        await b.upsert_file(_entry("shared.py"))
        await b.set_doc_path("shared.py", "docs/b.md")
        assert {e.doc_path for e in await b.files()} == {"docs/b.md"}
    async with await _store(db, _A) as a:
        assert {e.doc_path for e in await a.files()} == {"docs/a.md"}


async def test_roles_by_path_is_per_repo(tmp_path):
    db = tmp_path / "index.db"
    async with await _store(db, _A) as a:
        await a.upsert_file(_entry("shared.py", role=FileRole.PRODUCTION))
    async with await _store(db, _B) as b:
        await b.upsert_file(_entry("shared.py", role=FileRole.TEST))
        assert await b.roles_by_path() == {"shared.py": "test"}
    async with await _store(db, _A) as a:
        assert await a.roles_by_path() == {"shared.py": "production"}


async def test_clear_findings_for_rules_is_per_repo(tmp_path):
    db = tmp_path / "index.db"
    async with await _store(db, _A) as a:
        await a.add_findings("shared.py", [_finding("DUP")])
    async with await _store(db, _B) as b:
        await b.add_findings("shared.py", [_finding("DUP")])
        await b.clear_findings_for_rules(["DUP"])
        assert await b.all_findings() == []
    async with await _store(db, _A) as a:
        assert {f.rule_id for f in await a.all_findings()} == {"DUP"}  # A untouched


# --- shapes / duplicate detection are repo-scoped -------------------------------------------


async def test_duplicate_shapes_do_not_collide_across_repos(tmp_path):
    """The same shape hash in two different repos is NOT a cross-file duplicate — dedup must
    stay within a repo. This is the property the ``repo`` partition exists to guarantee."""
    db = tmp_path / "index.db"
    async with await _store(db, _A) as a:
        await a.add_shapes([("HH", "model", "a1.py", "M", 1)])
    async with await _store(db, _B) as b:
        await b.add_shapes([("HH", "model", "b1.py", "M", 1)])
        # only B's single occurrence of HH — not a duplicate despite A also having HH
        assert await b.duplicate_shapes() == {}

    # within one repo, two files sharing HH *is* a duplicate
    async with await _store(db, _A) as a:
        await a.add_shapes([("HH", "model", "a2.py", "M", 2)])
        dups = await a.duplicate_shapes()
        assert set(dups) == {"HH"}
        assert {r["path"] for r in dups["HH"]} == {"a1.py", "a2.py"}


async def test_clear_shapes_is_per_repo(tmp_path):
    db = tmp_path / "index.db"
    async with await _store(db, _A) as a:
        await a.add_shapes([("HH", "model", "shared.py", "M", 1)])
    async with await _store(db, _B) as b:
        await b.add_shapes([("HH", "model", "shared.py", "M", 1)])
        await b.clear_shapes("shared.py")
        assert await b.duplicate_shapes() == {}
    async with await _store(db, _A) as a:
        # A's shape row for shared.py survived B's clear
        await a.add_shapes([("HH", "model", "other.py", "M", 2)])
        assert "HH" in await a.duplicate_shapes()


# --- prune is scoped by repo AND prefix -----------------------------------------------------


async def test_prune_respects_repo_and_prefix(tmp_path):
    db = tmp_path / "index.db"
    async with await _store(db, _A) as a:
        for p in ("src/keep.py", "src/gone.py", "other/x.py"):
            await a.upsert_file(_entry(p))
    async with await _store(db, _B) as b:
        await b.upsert_file(_entry("src/gone.py"))  # same path, different repo

        async with await _store(db, _A) as a:
            pruned = await a.prune({"src/keep.py", "other/x.py"}, prefix="src/")
            assert pruned == ["src/gone.py"]  # only the stale file under the prefix
            remaining = {e.path for e in await a.files()}
            assert remaining == {
                "src/keep.py",
                "other/x.py",
            }  # other/ untouched by prefix

        assert {e.path for e in await b.files()} == {"src/gone.py"}  # B never pruned


@pytest.mark.parametrize("repo", [_A, _B, "."])
async def test_register_and_forget_roundtrip(tmp_path, repo):
    db = tmp_path / "index.db"
    async with await _store(db, repo) as s:
        await s.register(repo.rsplit("/", 1)[-1], 5.0)
        await s.upsert_file(_entry("x.py"))
        assert any(r["repo"] == repo for r in await s.repos())
        assert await s.forget() is True
        assert not any(r["repo"] == repo for r in await s.repos())
        assert {e.path for e in await s.files()} == set()  # cascade removed file rows
