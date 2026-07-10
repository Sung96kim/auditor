"""aggregate.py: build AUDIT.md from the index after a scan."""

from pathlib import Path

from auditor.aggregate import AuditAggregator
from auditor.config import load_config
from auditor.database import IndexStore
from auditor.engine import ScanEngine
from auditor.malware import passes
from auditor.malware.clamav import ClamDetection, ContentScanOutcome


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


async def test_aggregate_includes_malware_only_file(tmp_path, monkeypatch):
    """A malware-only file (no language claims it) has no ``files`` row unless the
    malware pass upserts one — without that row, `AuditAggregator` (which rebuilds
    results from `index.files.list()`) silently drops its findings."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="base"\n'
    )
    (root / ".auditor").mkdir()
    (root / "payload.bin").write_bytes(b"\x00INERT-SAMPLE\x00")

    monkeypatch.setattr(passes, "resolve_tool", lambda name: Path(f"/usr/bin/{name}"))
    monkeypatch.setattr(
        passes, "clamav_backend", lambda runner=None: "ClamAV 1.4.3/27700"
    )
    monkeypatch.setattr(
        passes,
        "run_content_scan",
        lambda files, **kwargs: ContentScanOutcome(
            detections=[
                ClamDetection(
                    path=str(root / "payload.bin"),
                    signature="Win.Trojan.Agent-1234567",
                    heuristic=False,
                )
            ],
            backend_binary="clamscan",
            ran=True,
        ),
    )

    settings = load_config(
        root, overrides={"malware_scan": {"enabled": True, "dependencies": False}}
    )
    db = root / ".auditor" / "index.db"
    async with await IndexStore.connect(db) as index:
        await ScanEngine.for_target(root, settings=settings, index=index).scan_path(
            root
        )
        assert await index.files.sha("payload.bin") is not None
        md = await AuditAggregator(index).markdown()
    assert "`payload.bin`" in md
    assert "blocking: 1" in md  # the AV-MAL-MATCH finding
