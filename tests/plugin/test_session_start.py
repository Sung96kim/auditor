import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "session_start.py"


def _run(payload: dict, path_has_auditr: bool, tmp_path: Path) -> str:
    env = {"PATH": "/usr/bin", "AUDITOR_OBSERVER": "0"}
    if path_has_auditr:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / "auditr").write_text("#!/bin/sh\n")
        (bin_dir / "auditr").chmod(0o755)
        env["PATH"] = f"{bin_dir}:/usr/bin"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout


def test_silent_when_auditr_absent(tmp_path):
    assert (
        _run({"cwd": str(tmp_path)}, path_has_auditr=False, tmp_path=tmp_path).strip()
        == ""
    )


def test_reports_available_and_configured(tmp_path):
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text("")
    out = _run({"cwd": str(tmp_path)}, path_has_auditr=True, tmp_path=tmp_path)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "This repo is configured" in ctx
    assert "/auditor:judge-findings" in ctx


def test_reports_configured_via_pyproject_tool_auditor(tmp_path):
    # no .auditor/config.toml, but pyproject.toml has a [tool.auditor] table
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="base"\n'
    )
    out = _run({"cwd": str(tmp_path)}, path_has_auditr=True, tmp_path=tmp_path)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "This repo is configured" in ctx
    assert "not yet configured" not in ctx


def test_reports_not_configured_when_no_config(tmp_path):
    # auditr present but no .auditor/config.toml → the not-configured branch
    out = _run({"cwd": str(tmp_path)}, path_has_auditr=True, tmp_path=tmp_path)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "not yet configured" in ctx
    assert "/auditor:setup" in ctx


def test_not_configured_on_malformed_pyproject(tmp_path):
    # a malformed pyproject.toml must not crash the hook — degrade to "not configured"
    (tmp_path / "pyproject.toml").write_text("not valid toml [[[")
    out = _run({"cwd": str(tmp_path)}, path_has_auditr=True, tmp_path=tmp_path)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "not yet configured" in ctx


def test_silent_on_non_dict_json(tmp_path):
    # valid JSON but not an object (e.g. 42) must not crash even with auditr present
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "auditr").write_text("#!/bin/sh\n")
    (bin_dir / "auditr").chmod(0o755)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="42",
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin"},
    )
    assert proc.stdout.strip() == ""
    assert proc.returncode == 0


def _run_with(payload: dict, path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": path},
    )


def test_delegates_the_attach_to_the_observer_client(recorder):
    stub = recorder("auditr-observer")
    payload = {"cwd": "/repo", "session_id": "s1"}
    assert _run_with(payload, stub.path()).returncode == 0
    assert stub.calls() == [
        {
            "argv": ["hook", "session-start", "--client", "claude-code"],
            "stdin": json.dumps(payload),
        }
    ]


def test_says_once_that_the_observer_client_is_not_installed():
    """No `uvx` ladder (P28): a hook budget cannot absorb a package resolve, so the honest answer
    is one line on stderr, from the one hook that runs once per session."""
    done = _run_with({"cwd": "/repo", "session_id": "s1"}, "/usr/bin")
    assert done.returncode == 0
    assert done.stdout.strip() == ""
    assert "auditr-observer" in done.stderr


def test_the_observer_runs_even_when_auditr_itself_is_absent(recorder, tmp_path):
    """The two halves are independent: `auditr` gates the context line, not the attach.

    Both sides of that claim, because only the absent one is what the delegation test above
    already covers: with `auditr` on PATH the same payload attaches *and* emits a context line,
    so the attach is what stays constant while the context line comes and goes.
    """
    stub = recorder("auditr-observer")
    absent = _run_with({"cwd": "/repo", "session_id": "s1"}, stub.path())
    assert absent.stdout.strip() == ""
    assert len(stub.calls()) == 1

    (stub.bin_dir / "auditr").write_text("#!/bin/sh\nexit 0\n")
    (stub.bin_dir / "auditr").chmod(0o755)
    present = _run_with({"cwd": str(tmp_path), "session_id": "s1"}, stub.path())
    assert "additionalContext" in present.stdout
    assert len(stub.calls()) == 2


def test_the_context_line_is_appended_to_never_substituted(tmp_path):
    """Spec 13.1: the shipped sentence survives, and the graph surfaces are added after it."""
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text("")
    out = _run({"cwd": str(tmp_path)}, path_has_auditr=True, tmp_path=tmp_path)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "/auditor:judge-findings" in ctx and "/auditor:audit-changes" in ctx
    assert ctx.index("/auditor:judge-findings") < ctx.index("/auditor:explore-graph")
    assert "/auditor:graph-observer" in ctx
