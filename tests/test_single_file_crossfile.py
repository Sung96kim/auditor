"""A single-file audit pulls cross-file findings off the shared index: it auto-warms a cold
index once, reuses a warm one without re-auditing peers, and skips the whole thing when isolated.
"""

import pytest

from auditor.engine import audit_target

_MODEL = (
    "from pydantic import BaseModel\n"
    "class Thing(BaseModel):\n"
    "    name: str\n"
    "    count: int\n"
    "    score: float\n"
)
_MODEL_DUP = _MODEL.replace(
    "Thing", "Widget"
)  # same field shape, different name → XFILE dup
_XFILE = "PY-XFILE-DUP-MODEL"


@pytest.fixture
def dup_repo(tmp_path, monkeypatch):
    """A repo with two duplicate pydantic models in separate files; shared index under tmp."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["pydantic"]\n'
        '[tool.auditor]\nextends="strict"\n'
    )
    (tmp_path / ".auditor").mkdir()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text(_MODEL)
    (pkg / "b.py").write_text(_MODEL_DUP)
    return tmp_path


def _rules(results, rel):
    res = next(r for r in results if r.file == rel)
    return {f.rule_id for f in res.findings}


async def test_cold_index_autowarms_and_finds_crossfile(dup_repo):
    """No prior scan: auditing a single file warms the repo once, so the cross-file dup surfaces."""
    results = await audit_target(dup_repo / "pkg" / "a.py", cross_file=True)
    assert [r.file for r in results] == ["pkg/a.py"]  # only the target is returned
    assert _XFILE in _rules(results, "pkg/a.py")


async def test_warm_index_single_file_gets_crossfile(dup_repo):
    """After the repo is indexed, a single-file audit pulls the cross-file dup off the index."""
    await audit_target(dup_repo, incremental=True)  # warm the index (scans both files)
    results = await audit_target(dup_repo / "pkg" / "a.py", cross_file=True)
    assert [r.file for r in results] == ["pkg/a.py"]
    assert _XFILE in _rules(results, "pkg/a.py")


async def test_isolated_skips_crossfile(dup_repo):
    """cross_file=False (the isolated opt-out, and the default) audits just the file — no
    cross-file findings, even with a warm index. Guards lookups like finding_detail too."""
    await audit_target(dup_repo, incremental=True)
    results = await audit_target(dup_repo / "pkg" / "a.py", cross_file=False)
    assert _XFILE not in _rules(results, "pkg/a.py")
