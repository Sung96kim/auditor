import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from auditor.discovery import find_root
from auditor.paths import ensure_repo_dir, repo_dir_key

SCRIPT = (
    Path(__file__).resolve().parents[2] / "plugin" / "statusline" / "auditor_status.py"
)


def _module():
    """Import the status line as a module, to compare its key with the package's."""
    spec = importlib.util.spec_from_file_location("auditor_status", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(cwd: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(cwd)}),
        capture_output=True,
        text=True,
    )
    return proc.stdout


def _write_status(cwd: Path, severity: dict, configured=True, age=0):
    """Write where a scan from ``cwd`` would: the status line walks up to the root first, so the
    helper has to as well or a nested-cwd test writes to the wrong key."""
    (ensure_repo_dir(find_root(cwd)) / "status.json").write_text(
        json.dumps(
            {
                "scan": {
                    "severity": severity,
                    "configured": configured,
                    "written_at": int(time.time()) - age,
                }
            }
        )
    )


def _write_raw(cwd: Path, raw: str) -> None:
    (ensure_repo_dir(find_root(cwd)) / "status.json").write_text(raw)


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
        '{"graph": {"nodes": 3}}',  # only the other writer's block
    ],
)
def test_corrupt_cache_degrades_to_not_set_up(tmp_path, raw):
    _write_raw(tmp_path, raw)
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
        '{"scan": {"severity": 5, "configured": true, "written_at": 0}}',
        '{"scan": {"severity": {"blocking": "x"}, "written_at": "soon"}}',
    ],
)
def test_malformed_fields_degrade_without_crashing(tmp_path, raw):
    _write_raw(tmp_path, raw)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr


def test_key_matches_the_package_helper(git_repo):
    """The status line has to land on the same directory `auditr scan` wrote."""
    assert _module()._repo_dir_key(git_repo) == repo_dir_key(git_repo)


def test_find_root_matches_the_package_helper(git_repo):
    """The other duplicated half: walk up the same way, or the key is computed for a different
    root and the two land in different directories."""
    nested = git_repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert _module()._find_root(nested) == find_root(nested)


def test_walks_up_to_the_repo_root(git_repo):
    _write_status(
        git_repo, {"blocking": 1, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    nested = git_repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert "1 blocking" in _run(nested)


def test_ignores_a_legacy_in_repo_status_file(tmp_path):
    legacy = tmp_path / ".auditor"
    legacy.mkdir()
    (legacy / ".status.json").write_text(
        json.dumps(
            {
                "severity": {"blocking": 9},
                "configured": True,
                "written_at": int(time.time()),
            }
        )
    )
    assert "not set up" in _run(tmp_path)
