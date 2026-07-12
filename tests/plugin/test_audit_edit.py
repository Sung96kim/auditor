import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "audit_edit.py"

REPORT = {
    "files": [
        {
            "file": "x.py",
            "findings": [
                {
                    "rule_id": "PY-SEC",
                    "severity": "blocking",
                    "verdict_kind": "candidate",
                    "line": 3,
                    "message": "danger",
                },
                {
                    "rule_id": "PY-STYLE",
                    "severity": "suggestion",
                    "verdict_kind": "auto",
                    "line": 9,
                    "message": "nit",
                },
            ],
        }
    ],
    "totals": {},
}


def _fake_auditr(tmp_path: Path, report: dict) -> Path:
    """A stub `auditr` on PATH that prints `report` as JSON for the `report` subcommand."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "auditr"
    stub.write_text(
        f"#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps({report!r}))\n"
    )
    stub.chmod(0o755)
    return bin_dir


def _run_full(
    file_path: str, tmp_path: Path, env_extra: dict, report=REPORT
) -> subprocess.CompletedProcess:
    bin_dir = _fake_auditr(tmp_path, report)
    env = {"PATH": f"{bin_dir}:/usr/bin", **env_extra}
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path},
        "cwd": str(tmp_path),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _run(file_path: str, tmp_path: Path, env_extra: dict, report=REPORT) -> str:
    return _run_full(file_path, tmp_path, env_extra, report).stdout


def test_surfaces_blocking_candidate_and_rolls_up(tmp_path):
    out = _run(str(tmp_path / "x.py"), tmp_path, {})
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "blocking" in ctx  # blocking candidate surfaced in detail
    assert "PY-SEC" in ctx
    assert "+1 suggestion" in ctx or "+1 lower" in ctx


def test_disabled_env_is_silent(tmp_path):
    assert (
        _run(str(tmp_path / "x.py"), tmp_path, {"AUDITOR_AUTOHOOK": "0"}).strip() == ""
    )


def test_unsupported_extension_is_silent(tmp_path):
    assert _run(str(tmp_path / "notes.txt"), tmp_path, {}).strip() == ""


def test_async_mode_emits_nothing(tmp_path):
    assert (
        _run(str(tmp_path / "x.py"), tmp_path, {"AUDITOR_AUTOHOOK_ASYNC": "1"}).strip()
        == ""
    )


@pytest.mark.parametrize("malformed_report", [[], {"files": None}])
def test_malformed_report_json_is_silent(tmp_path, malformed_report):
    proc = _run_full(str(tmp_path / "x.py"), tmp_path, {}, report=malformed_report)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
