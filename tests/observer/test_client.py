"""``auditr-observer``: stdlib-only and auditor-free; the verbs themselves live in test_daemon."""

import importlib.metadata
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
_NOTICE = "auditr-observer: not available in this release"


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
        ["bogus"],
        ["hook"],
        ["hook", "X", "--unknown"],
        ["--client"],
        ["ensure", "extra"],
    ],
)
def test_malformed_argv_reports_unavailable_and_still_exits_zero(
    argv: list[str], capsys: pytest.CaptureFixture[str]
):
    """Argparse exits 2 on these; propagating that would fail the very session hook."""
    assert auditr_observer.main(argv) == 0
    assert _NOTICE in capsys.readouterr().err


def test_version_flag_prints_the_installed_distribution_version(
    capsys: pytest.CaptureFixture[str],
):
    try:
        expected = importlib.metadata.version("auditr")
    except importlib.metadata.PackageNotFoundError:
        expected = "unknown (not installed as a distribution)"
    with pytest.raises(SystemExit) as exit_info:
        auditr_observer.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"auditr-observer {expected}"
