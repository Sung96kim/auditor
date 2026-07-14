import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "plugin" / "statusline" / "auditor_status.py"
)


def _run(cwd: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(cwd)}),
        capture_output=True,
        text=True,
    )
    return proc.stdout


def _write_status(cwd: Path, severity: dict, configured=True, age=0):
    d = cwd / ".auditor"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".status.json").write_text(
        json.dumps(
            {
                "severity": severity,
                "configured": configured,
                "written_at": int(time.time()) - age,
            }
        )
    )


def test_no_config_when_cache_absent(tmp_path):
    assert "not set up" in _run(tmp_path)


def test_clean_when_all_zero(tmp_path):
    _write_status(
        tmp_path, {"blocking": 0, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    assert "clean" in _run(tmp_path)


def test_spells_counts_and_rolls_lower(tmp_path):
    _write_status(
        tmp_path, {"blocking": 2, "high": 5, "medium": 4, "low": 3, "suggestion": 10}
    )
    out = _run(tmp_path)
    assert "2 blocking" in out and "5 high" in out and "+17 lower" in out


def test_stale_marker(tmp_path):
    _write_status(
        tmp_path,
        {"blocking": 1, "high": 0, "medium": 0, "low": 0, "suggestion": 0},
        age=3600,
    )
    assert "⟳" in _run(tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",  # decode error
        "[]",  # valid JSON, non-dict payload
    ],
)
def test_corrupt_cache_degrades_to_not_set_up(tmp_path, raw):
    d = tmp_path / ".auditor"
    d.mkdir(parents=True)
    (d / ".status.json").write_text(raw)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr
    assert "not set up" in proc.stdout


@pytest.mark.parametrize(
    "raw",
    [
        '{"severity": 5, "configured": true, "written_at": 0}',  # non-dict severity
        '{"severity": {"blocking": "x"}, "written_at": "soon"}',  # non-numeric fields
    ],
)
def test_malformed_fields_degrade_without_crashing(tmp_path, raw):
    d = tmp_path / ".auditor"
    d.mkdir(parents=True)
    (d / ".status.json").write_text(raw)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr
