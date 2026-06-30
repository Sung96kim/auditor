"""``auditor self update`` — unit tests (no network, no real installs)."""

import importlib.util
import json
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from _support import invoke

from auditor.cli.self_update import (
    fetch_pypi,
    is_newer,
    pick_latest,
    upgrade_command,
)

_RELEASES = ["0.1.0", "0.1.1", "0.2.0", "0.2.1"]
_RELEASES_WITH_RC = [*_RELEASES, "0.3.0rc1"]

_PYPI_FIXTURE = {
    "info": {"version": "0.2.1"},
    "releases": {v: [] for v in _RELEASES},
}


# ---------------------------------------------------------------------------
# pick_latest
# ---------------------------------------------------------------------------


def test_pick_latest_stable():
    assert pick_latest(_RELEASES, "0.2.1", include_pre=False) == "0.2.1"


def test_pick_latest_filters_rc_when_stable_available():
    assert pick_latest(_RELEASES_WITH_RC, "0.2.1", include_pre=False) == "0.2.1"


def test_pick_latest_includes_rc_when_pre_enabled():
    assert pick_latest(_RELEASES_WITH_RC, "0.2.1", include_pre=True) == "0.3.0rc1"


# ---------------------------------------------------------------------------
# is_newer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "installed,latest,expected",
    [
        ("0.2.1", "0.2.2", True),
        ("0.2.1", "0.2.1", False),
        ("0.2.1", "0.1.0", False),
    ],
)
def test_is_newer(installed: str, latest: str, expected: bool):
    assert is_newer(installed, latest) == expected


# ---------------------------------------------------------------------------
# fetch_pypi parsing
# ---------------------------------------------------------------------------


def test_fetch_pypi_parses_fixture(monkeypatch):
    payload = json.dumps(_PYPI_FIXTURE).encode()

    class _FakeResp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(
        "auditor.cli.self_update.urllib.request.urlopen",
        lambda *a, **kw: _FakeResp(),
    )

    result = fetch_pypi()
    assert result["info_version"] == "0.2.1"
    assert set(result["releases"]) == set(_RELEASES)


# ---------------------------------------------------------------------------
# upgrade_command
# ---------------------------------------------------------------------------


def test_upgrade_command_prefers_pip(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: MagicMock())
    cmd = upgrade_command()
    assert "-m" in cmd and "pip" in cmd


def test_upgrade_command_falls_back_to_uv(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    cmd = upgrade_command()
    assert cmd[0] == "uv" and "pip" in cmd


def test_upgrade_command_raises_when_neither_available(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="no pip/uv found"):
        upgrade_command()


# ---------------------------------------------------------------------------
# CLI: update --check
# ---------------------------------------------------------------------------


def test_check_flag_reports_update_available(monkeypatch):
    monkeypatch.setattr("auditor.cli.self_update.installed_version", lambda: "0.2.0")
    monkeypatch.setattr(
        "auditor.cli.self_update.fetch_pypi",
        lambda **kw: {"info_version": "0.2.1", "releases": _RELEASES},
    )
    monkeypatch.setattr(
        "auditor.cli.self_update.subprocess.Popen",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("subprocess must not be called")
        ),
    )

    result = invoke("self", "update", "--check")
    assert "update available" in result.output


def test_check_flag_reports_already_latest(monkeypatch):
    monkeypatch.setattr("auditor.cli.self_update.installed_version", lambda: "0.2.1")
    monkeypatch.setattr(
        "auditor.cli.self_update.fetch_pypi",
        lambda **kw: {"info_version": "0.2.1", "releases": _RELEASES},
    )

    result = invoke("self", "update", "--check")
    assert "up to date" in result.output


# ---------------------------------------------------------------------------
# CLI: install path
# ---------------------------------------------------------------------------


def test_update_yes_installs_and_reports_success(monkeypatch):
    fake_cmd = ["fake-pip", "install", "--upgrade", "auditr"]
    captured: list[list[str]] = []

    monkeypatch.setattr("auditor.cli.self_update.installed_version", lambda: "0.2.0")
    monkeypatch.setattr(
        "auditor.cli.self_update.fetch_pypi",
        lambda **kw: {"info_version": "0.2.1", "releases": _RELEASES},
    )
    monkeypatch.setattr("auditor.cli.self_update.upgrade_command", lambda: fake_cmd)
    monkeypatch.setattr(
        "auditor.cli.self_update.subprocess.Popen",
        lambda cmd, **kw: captured.append(cmd)
        or SimpleNamespace(returncode=0, poll=lambda: 0, wait=lambda: 0),
    )

    result = invoke("self", "update", "--yes")

    assert captured == [fake_cmd]
    assert "upgraded" in result.output
