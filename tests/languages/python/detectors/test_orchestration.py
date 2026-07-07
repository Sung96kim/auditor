"""Detectors in orchestration.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["orchestration"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


# ---------------------------------------------------------------------------
# LogicInCli — scope guards
# ---------------------------------------------------------------------------


def test_logic_in_cli_non_cli_module_quiet():
    # the same work calls outside a CLI command module are FreeFnOrchestrator territory
    src = (
        "import shutil\n"
        "import subprocess\n"
        "def install(target: str) -> None:\n"
        "    subprocess.run(['git', 'clone', REPO, target], check=True)\n"
        "    shutil.copytree(f'{target}/assets', '/opt/assets')\n"
        "    subprocess.run(['make', 'build'], cwd=target, check=True)\n"
    )
    assert "PY-OOP-LOGIC-IN-CLI" not in rule_ids(run_audit(src))


def test_logic_in_cli_write_mode_open_counts():
    # open() counts only in a write mode — presentation-ish reads stay free
    src = (
        "import subprocess\n"
        "import typer\n"
        "@app.command()\n"
        "def setup(path: str) -> None:\n"
        "    subprocess.run(['mkdir', '-p', path])\n"
        "    with open(f'{path}/config.toml', 'w') as fh:\n"
        "        fh.write(DEFAULTS)\n"
        "    subprocess.run(['systemctl', 'restart', 'svc'])\n"
    )
    assert "PY-OOP-LOGIC-IN-CLI" in rule_ids(run_audit(src))
