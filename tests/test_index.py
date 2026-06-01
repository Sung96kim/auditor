"""index.py: IndexStore scope, per-rule cache, findings, shapes (direct unit tests)."""

import time

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
        rule_id=rule, category=Category.SECURITY, severity=sev,
        verdict_kind=VerdictKind.AUTO, line=2, message="eval",
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
            IndexEntry(path="a.py", sha256="h1", lines=3, language="python",
                       role=FileRole.PRODUCTION, last_scanned=now)
        )
        await index.record_rule("a.py", "PY-SEC-DANGEROUS-EVAL", "fp1", [_finding()], now)
        assert await index.file_sha("a.py") == "h1"
        assert await index.rule_fingerprint("a.py", "PY-SEC-DANGEROUS-EVAL") == "fp1"
        cached = await index.cached_findings("a.py", "PY-SEC-DANGEROUS-EVAL")
        assert len(cached) == 1 and cached[0].rule_id == "PY-SEC-DANGEROUS-EVAL"
        files = await index.files()
        assert files[0].counts.get("blocking") == 1


async def test_record_rule_replaces(tmp_path):
    async with await IndexStore.connect(tmp_path / "i.db") as index:
        now = time.time()
        await index.upsert_file(
            IndexEntry(path="a.py", sha256="h", lines=1, language="python",
                       role=FileRole.PRODUCTION, last_scanned=now)
        )
        await index.record_rule("a.py", "R", "fp", [_finding("R"), _finding("R")], now)
        await index.record_rule("a.py", "R", "fp", [], now)  # re-run, no findings
        assert await index.cached_findings("a.py", "R") == []
        assert await index.rule_fingerprint("a.py", "R") == "fp"  # ran-clean, not absent


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
