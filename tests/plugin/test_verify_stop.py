import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "verify_stop.py"

# A real gate trip: the scan completes and emits its JSON report, then exits 1.
_TRIP_PAYLOAD = json.dumps(
    {
        "files": [{"file": "x.py", "findings": [{"severity": "high"}]}],
        "totals": {"high": 1},
    }
)
_CLEAN_PAYLOAD = json.dumps({"files": [], "totals": {}})


def _stub(tmp_path: Path, *, stdout: str, exit_code: int) -> Path:
    """A fake `auditr` on PATH that writes `stdout` and exits `exit_code`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "auditr"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        f"import sys\nsys.stdout.write({stdout!r})\nraise SystemExit({exit_code})\n"
    )
    stub.chmod(0o755)
    return bin_dir


def _run(
    bin_dir: Path, tmp_path: Path, *, enabled: bool
) -> subprocess.CompletedProcess:
    env = {"PATH": f"{bin_dir}:/usr/bin"}
    if enabled:
        env["AUDITOR_VERIFY_HOOK"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
        env=env,
    )


def test_disabled_by_default(tmp_path):
    bin_dir = _stub(tmp_path, stdout=_TRIP_PAYLOAD, exit_code=1)
    assert _run(bin_dir, tmp_path, enabled=False).stdout.strip() == ""


def test_blocks_on_real_gate_trip(tmp_path):
    # scan payload on stdout + nonzero exit = real trip → block with the findings message
    bin_dir = _stub(tmp_path, stdout=_TRIP_PAYLOAD, exit_code=1)
    decision = json.loads(_run(bin_dir, tmp_path, enabled=True).stdout)
    assert decision["decision"] == "block"
    assert "gate" in decision["reason"]
    assert "judge-findings" in decision["reason"]


def test_allows_when_clean(tmp_path):
    bin_dir = _stub(tmp_path, stdout=_CLEAN_PAYLOAD, exit_code=0)
    assert _run(bin_dir, tmp_path, enabled=True).stdout.strip() == ""


def test_couldnt_evaluate_does_not_block(tmp_path):
    # auditr errored (not a git repo / bad args): nonzero exit but NO scan payload on stdout.
    # Must NOT block (a tool hiccup can't wedge the agent) — surface a systemMessage instead.
    bin_dir = _stub(tmp_path, stdout="fatal: not a git repository\n", exit_code=1)
    decision = json.loads(_run(bin_dir, tmp_path, enabled=True).stdout)
    assert "decision" not in decision  # not blocked
    assert "couldn't evaluate" in decision["systemMessage"]


def test_couldnt_evaluate_on_usage_error(tmp_path):
    # exit >=2 (bad CLI flags) with empty stdout → couldn't evaluate, non-blocking
    bin_dir = _stub(tmp_path, stdout="", exit_code=2)
    decision = json.loads(_run(bin_dir, tmp_path, enabled=True).stdout)
    assert "decision" not in decision
    assert "couldn't evaluate" in decision["systemMessage"]


def test_silent_on_non_dict_stdin(tmp_path):
    # valid JSON but not an object (e.g. 42) must not crash even when enabled + auditr present
    bin_dir = _stub(tmp_path, stdout=_TRIP_PAYLOAD, exit_code=1)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="42",
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin", "AUDITOR_VERIFY_HOOK": "1"},
    )
    assert proc.stdout.strip() == ""
    assert proc.returncode == 0


def test_silent_when_auditr_absent(tmp_path):
    # AUDITOR_VERIFY_HOOK=1 but auditr missing from PATH: hook must stay silent
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
        env={"PATH": str(empty_bin), "AUDITOR_VERIFY_HOOK": "1"},
    )
    assert proc.stdout.strip() == ""
    assert proc.returncode == 0
