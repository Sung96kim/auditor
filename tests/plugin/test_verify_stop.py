import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "verify_stop.py"


def _fake_auditr(tmp_path: Path, gate_tripped: bool) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "auditr"
    payload = {"gate": {"fail_on": "high", "tripped": gate_tripped}, "totals": {}}
    stub.write_text(
        f"#!/usr/bin/env python3\nimport json\nprint(json.dumps({payload!r}))\n"
    )
    stub.chmod(0o755)
    return bin_dir


def _fake_auditr_non_dict(tmp_path: Path, exit_code: int) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "auditr"
    stub.write_text(
        f"#!/usr/bin/env python3\nprint('not json')\nraise SystemExit({exit_code})\n"
    )
    stub.chmod(0o755)
    return bin_dir


def _run(tmp_path: Path, enabled: bool, gate_tripped: bool) -> str:
    bin_dir = _fake_auditr(tmp_path, gate_tripped)
    env = {"PATH": f"{bin_dir}:/usr/bin"}
    if enabled:
        env["AUDITOR_VERIFY_HOOK"] = "1"
    payload = {"cwd": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout


def test_disabled_by_default(tmp_path):
    assert _run(tmp_path, enabled=False, gate_tripped=True).strip() == ""


def test_blocks_when_gate_tripped(tmp_path):
    out = _run(tmp_path, enabled=True, gate_tripped=True)
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert isinstance(decision["reason"], str)
    assert decision["reason"]


def test_allows_when_clean(tmp_path):
    assert _run(tmp_path, enabled=True, gate_tripped=False).strip() == ""


def test_silent_on_non_dict_json(tmp_path):
    # valid JSON but not an object (e.g. 42) must not crash even when enabled + auditr present
    bin_dir = _fake_auditr(tmp_path, gate_tripped=True)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="42",
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin", "AUDITOR_VERIFY_HOOK": "1"},
    )
    assert proc.stdout.strip() == ""
    assert proc.returncode == 0


def test_silent_when_auditr_not_on_path(tmp_path):
    # AUDITOR_VERIFY_HOOK=1 but auditr missing from PATH: hook must stay silent
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()
    payload = {"cwd": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": str(empty_bin), "AUDITOR_VERIFY_HOOK": "1"},
    )
    assert proc.stdout.strip() == ""
    assert proc.returncode == 0


def test_blocks_on_non_json_stdout_and_nonzero_exit(tmp_path):
    # non-JSON auditr stdout + nonzero exit must fall back to the exit code and block
    bin_dir = _fake_auditr_non_dict(tmp_path, exit_code=1)
    payload = {"cwd": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin", "AUDITOR_VERIFY_HOOK": "1"},
    )
    assert json.loads(proc.stdout)["decision"] == "block"


def test_allows_on_non_dict_json_stdout_and_zero_exit(tmp_path):
    # valid JSON but not a dict (e.g. a bare list) + exit 0 must not crash and must not block
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "auditr"
    stub.write_text("#!/usr/bin/env python3\nprint('[]')\n")
    stub.chmod(0o755)
    payload = {"cwd": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin", "AUDITOR_VERIFY_HOOK": "1"},
    )
    assert proc.stdout.strip() == ""
    assert proc.returncode == 0
