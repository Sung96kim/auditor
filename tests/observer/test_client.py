"""``auditr-observer``: stdlib-only, auditor-free, and inert until the daemon slice lands."""

import subprocess
import sys
from pathlib import Path

import pytest

import auditr_observer
from auditor.observer import OBSERVER_API_VERSION

_ROOT = Path(__file__).resolve().parents[2]
_PROBE = (
    "import sys, auditr_observer; "
    "print([m for m in sys.modules if m == 'auditor' or m.startswith('auditor.')])"
)


def test_api_version_literals_match():
    assert auditr_observer.OBSERVER_API_VERSION == OBSERVER_API_VERSION == 1


def test_client_imports_no_auditor_module():
    """The hook client runs on every session event; importing auditor would cost ~0.23 s."""
    probe = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "[]"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["ensure"],
        ["start"],
        ["stop"],
        ["status"],
        ["open"],
        ["hook", "SessionStart", "--client", "claude"],
    ],
)
def test_every_subcommand_reports_unavailable_and_exits_zero(
    argv: list[str], capsys: pytest.CaptureFixture[str]
):
    assert auditr_observer.main(argv) == 0
    assert "auditr-observer: not available in this release" in capsys.readouterr().err


def test_version_flag_prints_the_installed_distribution_version(
    capsys: pytest.CaptureFixture[str],
):
    with pytest.raises(SystemExit) as exit_info:
        auditr_observer.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("auditr-observer ")
