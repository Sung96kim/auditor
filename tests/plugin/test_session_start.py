import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "session_start.py"


def _run(payload: dict, path_has_auditr: bool, tmp_path: Path) -> str:
    env = {"PATH": "/usr/bin"}
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
    assert "configured" in ctx
    assert "/auditor:judge-findings" in ctx
