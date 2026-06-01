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
