"""End-to-end ignore behavior through audit_target: a stored ignore hides the finding on rescan,
sets ScanResult.ignored, show_ignored reveals it, and a stateless scan with no ignores is a
no-op (and doesn't create the shared db)."""

from pathlib import Path

from auditor.database import IndexStore
from auditor.engine import audit_target
from auditor.paths import index_db_path, repo_key


def _make_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="base"\n'
    )
    (root / "mod.py").write_text("password = 'hunter2'\n")  # PY-SEC-HARDCODED-SECRET
    return root


async def _add_ignore(root: Path, rule_id: str, file=None, line=None, ev=None) -> None:
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        await index.ignores.add_ignore(rule_id, file, line, ev, None, 1.0)


def _rules(results):
    return {f.rule_id for r in results for f in r.findings}


async def test_stored_ignore_hides_finding_on_rescan(tmp_path):
    repo = _make_repo(tmp_path / "r")
    assert "PY-SEC-HARDCODED-SECRET" in _rules(await audit_target(repo, root=repo))

    await _add_ignore(repo, "PY-SEC-HARDCODED-SECRET")
    results = await audit_target(repo, root=repo)
    assert "PY-SEC-HARDCODED-SECRET" not in _rules(results)
    assert sum(r.ignored for r in results) == 1


async def test_show_ignored_reveals_but_counts(tmp_path):
    repo = _make_repo(tmp_path / "r")
    await _add_ignore(repo, "PY-SEC-HARDCODED-SECRET")
    results = await audit_target(repo, root=repo, show_ignored=True)
    assert "PY-SEC-HARDCODED-SECRET" in _rules(results)  # shown
    assert sum(r.ignored for r in results) == 1  # but flagged ignored


async def test_apply_ignores_false_skips_filtering(tmp_path):
    repo = _make_repo(tmp_path / "r")
    await _add_ignore(repo, "PY-SEC-HARDCODED-SECRET")
    results = await audit_target(repo, root=repo, apply_ignores=False)
    assert "PY-SEC-HARDCODED-SECRET" in _rules(results)
    assert sum(r.ignored for r in results) == 0


async def test_file_scoped_ignore_only_that_file(tmp_path):
    repo = _make_repo(tmp_path / "r")
    (repo / "other.py").write_text("token = 'abcdef123456'\n")  # also hardcoded secret
    await _add_ignore(repo, "PY-SEC-HARDCODED-SECRET", file="mod.py")
    results = await audit_target(repo, root=repo)
    by_file = {r.file: {f.rule_id for f in r.findings} for r in results}
    assert "PY-SEC-HARDCODED-SECRET" not in by_file.get("mod.py", set())  # ignored
    assert "PY-SEC-HARDCODED-SECRET" in by_file.get("other.py", set())  # still flagged


async def test_stateless_scan_no_ignores_creates_no_db(tmp_path):
    repo = _make_repo(tmp_path / "r")
    await audit_target(repo, root=repo)  # no ignores added, non-incremental
    assert not index_db_path().exists()  # ignore-loading didn't create the shared db
