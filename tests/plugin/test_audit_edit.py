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
    env = {"PATH": f"{bin_dir}:/usr/bin", "AUDITOR_OBSERVER": "0", **env_extra}
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


def _run_observed(
    payload: dict, path: str, env_extra: dict
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": path, **env_extra},
    )


def test_delegates_every_edit_to_the_observer_client(recorder):
    stub = recorder("auditr-observer")
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "/repo/m.py"},
        "cwd": "/repo",
    }
    assert _run_observed(payload, stub.path(), {}).returncode == 0
    assert stub.calls() == [
        {
            "argv": ["hook", "post-tool-use", "--client", "claude-code"],
            "stdin": json.dumps(payload),
        }
    ]


def test_the_observer_half_survives_the_audit_kill_switch(recorder):
    """`AUDITOR_AUTOHOOK` turns off the inline audit; `AUDITOR_OBSERVER` is the observer's own."""
    stub = recorder("auditr-observer")
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "/repo/m.py"},
        "cwd": "/repo",
    }
    done = _run_observed(payload, stub.path(), {"AUDITOR_AUTOHOOK": "0"})
    assert done.stdout.strip() == ""
    assert len(stub.calls()) == 1


def test_the_observer_sees_a_suffix_the_audit_half_ignores(recorder):
    """`SUFFIXES` here is the audit's seven; Stage 0 is the client's, and it is wider."""
    stub = recorder("auditr-observer")
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "/repo/config.toml"},
        "cwd": "/repo",
    }
    assert _run_observed(payload, stub.path(), {}).stdout.strip() == ""
    assert len(stub.calls()) == 1


@pytest.mark.parametrize(
    ("value", "spawns"),
    [
        ("0", 0),
        ("f", 0),
        ("false", 0),
        ("n", 0),
        ("no", 0),
        ("off", 0),
        ("1", 1),
        ("", 1),
    ],
)
def test_the_observer_kill_switch_stops_the_spawn_itself(recorder, value, spawns):
    """`AUDITOR_OBSERVER` has to switch off the process, not only what the process does.

    Read inside the client alone it would cost a spawn on every Edit, every Write, every Stop and
    every session boundary in order to be told the observer is off (P27). The six off values are
    `auditr_observer._OFF`'s; `plugin/` may not import it, so the pair is pinned behaviourally.
    """
    stub = recorder("auditr-observer")
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "/repo/m.py"},
        "cwd": "/repo",
    }
    done = _run_observed(payload, stub.path(), {"AUDITOR_OBSERVER": value})
    assert done.returncode == 0
    assert len(stub.calls()) == spawns
