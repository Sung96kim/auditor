"""SessionEnd hook: one detach, and silence when no observer client is installed."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "session_end.py"


def _run(payload: str, path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        env={"PATH": path},
    )


def test_hands_the_payload_to_the_observer_client(recorder):
    stub = recorder("auditr-observer")
    payload = json.dumps({"session_id": "s1", "cwd": "/repo"})
    done = _run(payload, stub.path())
    assert done.returncode == 0
    assert done.stdout == ""
    assert stub.calls() == [
        {
            "argv": ["hook", "session-end", "--client", "claude-code"],
            "stdin": payload,
        }
    ]


def test_silent_when_no_observer_client_is_installed(tmp_path: Path):
    done = _run(json.dumps({"session_id": "s1"}), "/usr/bin")
    assert done.returncode == 0
    assert done.stdout == ""


def test_silent_on_a_payload_that_is_not_an_object(recorder):
    stub = recorder("auditr-observer")
    assert _run("42", stub.path()).returncode == 0
    assert stub.calls() == []
