"""The status cache: rolled-up severity counts written under the user home, merged block by
block so two writers never clobber each other, and never a byte inside the repo."""

import json
import os
import time
from pathlib import Path

from auditor import status
from auditor.models import (
    Category,
    FileRole,
    Finding,
    ScanResult,
    Severity,
    VerdictKind,
)
from auditor.status import _lock, _merge_status, status_path, write_status


def _result(sev: Severity) -> ScanResult:
    return ScanResult(
        file="x.py",
        language="python",
        role=FileRole.PRODUCTION,
        findings=[
            Finding(
                rule_id="PY-TEST",
                category=Category.CORRECTNESS,
                severity=sev,
                verdict_kind=VerdictKind.AUTO,
                line=1,
                message="m",
            )
        ],
    )


def _tree(root: Path) -> set[Path]:
    return set(root.rglob("*"))


def test_write_status_rolls_up_counts(tmp_path: Path):
    out = write_status(
        tmp_path,
        [_result(Severity.HIGH), _result(Severity.HIGH), _result(Severity.LOW)],
        configured=True,
    )
    assert out == status_path(tmp_path)
    scan = json.loads(out.read_text())["scan"]
    assert scan["severity"]["high"] == 2
    assert scan["severity"]["low"] == 1
    assert scan["severity"]["blocking"] == 0
    assert scan["configured"] is True
    assert isinstance(scan["written_at"], int)


def test_write_status_writes_nothing_under_root(tmp_path: Path):
    before = _tree(tmp_path)
    write_status(tmp_path, [_result(Severity.HIGH)], configured=True)
    assert _tree(tmp_path) == before  # invariant 6: the repo is never written to


def test_merge_status_preserves_a_foreign_block(tmp_path: Path):
    _merge_status(tmp_path, "graph", {"nodes": 5})
    write_status(tmp_path, [_result(Severity.LOW)], configured=False)
    data = json.loads(status_path(tmp_path).read_text())
    assert data["graph"] == {"nodes": 5}
    assert data["scan"]["severity"]["low"] == 1


def test_merge_status_replaces_only_its_own_block(tmp_path: Path):
    write_status(tmp_path, [_result(Severity.HIGH)], configured=True)
    _merge_status(tmp_path, "scan", {"severity": {}, "configured": False})
    data = json.loads(status_path(tmp_path).read_text())
    assert data["scan"] == {"severity": {}, "configured": False}


def test_merge_status_breaks_a_stale_lock(tmp_path: Path):
    lock = status_path(tmp_path).parent
    lock.mkdir(parents=True, exist_ok=True)
    stale = lock / "status.lock"
    stale.write_text("")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    _merge_status(tmp_path, "graph", {"nodes": 1})
    assert json.loads(status_path(tmp_path).read_text())["graph"] == {"nodes": 1}
    assert not stale.exists()


def test_write_status_swallows_oserror_on_unwritable_home(tmp_path: Path, monkeypatch):
    blocked = tmp_path / "home"
    blocked.write_text("not a directory")  # mkdir under it raises OSError
    monkeypatch.setenv("AUDITOR_HOME", str(blocked))
    out = write_status(tmp_path, [_result(Severity.HIGH)], configured=True)
    assert out == status_path(tmp_path)
    assert not out.exists()


def test_torn_status_file_is_replaced_not_merged(tmp_path: Path):
    path = status_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    write_status(tmp_path, [_result(Severity.HIGH)], configured=True)
    assert json.loads(path.read_text())["scan"]["severity"]["high"] == 1


def test_severity_tiers_come_from_the_enum(tmp_path: Path):
    """The tiers were a hand-copied tuple, so a new Severity member would land in the file
    through a .get fallback and be dropped by the status line's fixed split."""
    written = json.loads(write_status(tmp_path, [], configured=False).read_text())
    assert set(written["scan"]["severity"]) == {sev.value for sev in Severity}


def test_lock_only_removes_the_file_it_still_holds(tmp_path: Path):
    """A waiter that broke our lock owns the one on disk now; unlinking it hands a third writer
    the lock while the second is mid-write."""
    path = tmp_path / "status.lock"
    with _lock(path):
        path.unlink()
        path.write_text("the waiter's lock")
    assert path.exists()


def test_lock_sleeps_on_every_retry_past_the_deadline(tmp_path: Path, monkeypatch):
    """Past the deadline the loop skipped the sleep, so racing a live writer spun on a core."""
    path = tmp_path / "status.lock"
    path.write_text("")
    monkeypatch.setattr(status, "_LOCK_TIMEOUT_S", 0.0)
    rounds: list[float] = []

    def _sleep(seconds: float) -> None:
        rounds.append(seconds)
        if len(rounds) < 3:
            path.write_text("")  # a competitor takes it again

    monkeypatch.setattr(time, "sleep", _sleep)
    with _lock(path):
        pass
    assert len(rounds) == 3


def test_tmp_file_is_per_process(tmp_path: Path, monkeypatch):
    """Two processes sharing status.json.tmp can clobber each other's half-written file."""
    seen: list[str] = []
    real = Path.write_text

    def spy(self, *args, **kwargs):
        seen.append(self.name)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    _merge_status(tmp_path, "graph", {"nodes": 1})
    assert f"status.json.{os.getpid()}.tmp" in seen
