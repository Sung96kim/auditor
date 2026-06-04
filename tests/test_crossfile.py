"""Cross-file dedup: duplicate models/functions across files flag both sites; a unique
shape stays clean; editing one file re-runs the GROUP BY without re-parsing the other."""

from pathlib import Path

from auditor.config import load_config
from auditor.engine import ScanEngine
from auditor.index import IndexStore

_MODEL = (
    "from pydantic import BaseModel\n"
    "class Thing(BaseModel):\n"
    "    name: str\n"
    "    count: int\n"
    "    score: float\n"
)
_MODEL_DUP = _MODEL.replace("Thing", "Widget")  # same field shape, different name
_UNIQUE = (
    "from pydantic import BaseModel\n"
    "class Other(BaseModel):\n"
    "    a: str\n"
    "    b: str\n"
    "    c: str\n"
    "    d: str\n"
)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["pydantic"]\n[tool.auditor]\nextends="strict"\n'
    )
    (tmp_path / ".auditor").mkdir()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    return pkg


async def test_duplicate_model_flags_both(tmp_path):
    pkg = _repo(tmp_path)
    (pkg / "a.py").write_text(_MODEL)
    (pkg / "b.py").write_text(_MODEL_DUP)
    (pkg / "c.py").write_text(_UNIQUE)
    settings = load_config(tmp_path)
    async with await IndexStore.connect(tmp_path / ".auditor" / "index.db") as index:
        results = {
            r.file: r
            for r in await ScanEngine.for_target(
                pkg, settings=settings, index=index
            ).scan_path(pkg)
        }
    a_rules = {f.rule_id for f in results["pkg/a.py"].findings}
    b_rules = {f.rule_id for f in results["pkg/b.py"].findings}
    c_rules = {f.rule_id for f in results["pkg/c.py"].findings}
    assert "PY-XFILE-DUP-MODEL" in a_rules
    assert "PY-XFILE-DUP-MODEL" in b_rules
    assert "PY-XFILE-DUP-MODEL" not in c_rules


_FUNC = (
    "def summarize(items):\n"
    "    out = []\n"
    "    for it in items:\n"
    "        out.append(transform(it))\n"
    "    return out\n"
)
_FUNC_RENAMED = (  # same code, renamed identifiers -> same normalized shape (a real clone)
    "def process(rows):\n"
    "    acc = []\n"
    "    for row in rows:\n"
    "        acc.append(transform(row))\n"
    "    return acc\n"
)
_FUNC_LOOKALIKE = (  # identical statement *skeleton* but a different call -> must NOT collide
    "def collect(items):\n"
    "    out = []\n"
    "    for it in items:\n"
    "        out.append(validate(it))\n"
    "    return out\n"
)


async def test_duplicate_function_flags_clones_not_lookalikes(tmp_path):
    pkg = _repo(tmp_path)
    (pkg / "a.py").write_text(_FUNC)
    (pkg / "b.py").write_text(_FUNC_RENAMED)
    (pkg / "c.py").write_text(_FUNC_LOOKALIKE)
    settings = load_config(tmp_path)
    async with await IndexStore.connect(tmp_path / ".auditor" / "index.db") as index:
        results = {
            r.file: {x.rule_id for x in r.findings}
            for r in await ScanEngine.for_target(
                pkg, settings=settings, index=index
            ).scan_path(pkg)
        }
    assert "PY-XFILE-DUP-FUNCTION" in results["pkg/a.py"]  # renamed clone collides
    assert "PY-XFILE-DUP-FUNCTION" in results["pkg/b.py"]
    # same [Assign, For, Return] skeleton but a different call — the precise shape ignores it
    assert "PY-XFILE-DUP-FUNCTION" not in results["pkg/c.py"]


async def test_crossfile_runs_stateless_without_an_index(tmp_path):
    # a plain `scan <dir>` (no --incremental, no index) still surfaces XFILE findings, in memory
    pkg = _repo(tmp_path)
    (pkg / "a.py").write_text(_FUNC)
    (pkg / "b.py").write_text(_FUNC_RENAMED)
    settings = load_config(tmp_path)
    results = await ScanEngine.for_target(pkg, settings=settings).scan_path(pkg)
    flagged = {
        r.file
        for r in results
        if "PY-XFILE-DUP-FUNCTION" in {f.rule_id for f in r.findings}
    }
    assert flagged == {"pkg/a.py", "pkg/b.py"}


async def test_duplicate_method_across_files_flags(tmp_path):
    # a method copy-pasted into a class in another file is caught too (methods are now indexed)
    pkg = _repo(tmp_path)
    method = (
        "    def handle(self, evt):\n"
        "        data = parse(evt)\n"
        "        save(data)\n"
        "        return data\n"
    )
    (pkg / "d.py").write_text("class Alpha:\n" + method)
    (pkg / "e.py").write_text("class Beta:\n" + method)
    settings = load_config(tmp_path)
    async with await IndexStore.connect(tmp_path / ".auditor" / "index.db") as index:
        results = {
            r.file: {x.rule_id for x in r.findings}
            for r in await ScanEngine.for_target(
                pkg, settings=settings, index=index
            ).scan_path(pkg)
        }
    assert "PY-XFILE-DUP-FUNCTION" in results["pkg/d.py"]
    assert "PY-XFILE-DUP-FUNCTION" in results["pkg/e.py"]


async def test_xfile_method_min_statements_is_configurable(tmp_path):
    # raising threshold.dry.xfile_method_min_statements above the method's size stops it being
    # indexed for dedup (and thus flagged) — proving the knob is wired, not a magic constant
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="strict"\n'
        "[tool.auditor.threshold.dry]\nxfile_method_min_statements = 99\n"
    )
    (tmp_path / ".auditor").mkdir()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    method = (
        "    def handle(self, evt):\n"
        "        data = parse(evt)\n"
        "        save(data)\n"
        "        return data\n"
    )
    (pkg / "d.py").write_text("class Alpha:\n" + method)
    (pkg / "e.py").write_text("class Beta:\n" + method)
    settings = load_config(tmp_path)
    async with await IndexStore.connect(tmp_path / ".auditor" / "index.db") as index:
        results = {
            r.file: {x.rule_id for x in r.findings}
            for r in await ScanEngine.for_target(
                pkg, settings=settings, index=index
            ).scan_path(pkg)
        }
    assert "PY-XFILE-DUP-FUNCTION" not in results["pkg/d.py"]
    assert "PY-XFILE-DUP-FUNCTION" not in results["pkg/e.py"]


async def test_within_role_scoping(tmp_path):
    # a prod model and a same-shape model in a test file should NOT be flagged as dups
    pkg = _repo(tmp_path)
    (pkg / "a.py").write_text(_MODEL)
    tests_dir = pkg / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_x.py").write_text(
        "import pytest\n" + _MODEL_DUP + "\ndef test_z():\n    assert True\n"
    )
    settings = load_config(tmp_path)
    async with await IndexStore.connect(tmp_path / ".auditor" / "index.db") as index:
        results = {
            r.file: r
            for r in await ScanEngine.for_target(
                pkg, settings=settings, index=index
            ).scan_path(pkg)
        }
    a_rules = {f.rule_id for f in results["pkg/a.py"].findings}
    assert "PY-XFILE-DUP-MODEL" not in a_rules
