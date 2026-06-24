"""aggregate.py: build AUDIT.md from the index after a scan."""

from pathlib import Path

from auditor.aggregate import AuditAggregator
from auditor.config import load_config
from auditor.database import IndexStore
from auditor.engine import ScanEngine


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["pydantic"]\n[tool.auditor]\nextends="base"\n'
    )
    (tmp_path / ".auditor").mkdir()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("def f(x):\n    eval(x)\n    return x\n")
    (pkg / "clean.py").write_text("def g(y: int) -> int:\n    return y\n")
    return tmp_path


async def test_aggregate_rollup(tmp_path):
    root = _repo(tmp_path)
    settings = load_config(root)
    db = root / ".auditor" / "index.db"
    async with await IndexStore.connect(db) as index:
        await ScanEngine.for_target(
            root / "pkg", settings=settings, index=index
        ).scan_path(root / "pkg")
        md = await AuditAggregator(index).markdown()
    assert "# Audit — consolidated report" in md
    assert "Scope: 2 files audited." in md
    assert "`pkg/a.py`" in md
    assert "blocking: 1" in md  # the eval finding


async def test_write_audit_file(tmp_path):
    root = _repo(tmp_path)
    settings = load_config(root)
    db = root / ".auditor" / "index.db"
    out = root / "AUDIT.md"
    async with await IndexStore.connect(db) as index:
        await ScanEngine.for_target(
            root / "pkg", settings=settings, index=index
        ).scan_path(root / "pkg")
        await AuditAggregator(index).write(out)
    assert out.exists()
    assert "consolidated report" in out.read_text()
