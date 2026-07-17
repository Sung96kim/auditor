"""``auditor self update`` — unit tests (no network, no real installs)."""

import importlib.util
import json
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from _support import invoke

from auditor.cli.self_update import (
    InstallContext,
    fetch_pypi,
    guard_extras,
    install_context,
    is_newer,
    pick_latest,
    upgrade_command,
)

_RELEASES = ["0.1.0", "0.1.1", "0.2.0", "0.2.1"]
_RELEASES_WITH_RC = [*_RELEASES, "0.3.0rc1"]

_PYPI_FIXTURE = {
    "info": {"version": "0.2.1", "provides_extra": ["graph", "mcp", "ts"]},
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
    assert result["provides_extra"] == ["graph", "mcp", "ts"]


# ---------------------------------------------------------------------------
# guard_extras — drop extras the target release no longer provides
# ---------------------------------------------------------------------------


def test_guard_extras_drops_removed():
    kept, dropped = guard_extras(["mcp", "graph", "ts"], ["mcp", "graph"])
    assert kept == ["graph", "mcp"]  # sorted, still-provided
    assert dropped == ["ts"]  # removed upstream


def test_guard_extras_none_provided_keeps_all():
    # provides_extra unknown (older metadata) → best-effort, keep everything, drop nothing
    kept, dropped = guard_extras(["mcp", "graph"], None)
    assert kept == ["graph", "mcp"] and dropped == []


def test_guard_extras_empty_provided_drops_all():
    # target release genuinely provides no extras → request none (don't hard-fail on install)
    kept, dropped = guard_extras(["mcp"], [])
    assert kept == [] and dropped == ["mcp"]


def test_guard_extras_normalizes_names():
    kept, dropped = guard_extras(["code_mode"], ["code-mode"])
    assert kept == ["code-mode"] and dropped == []


# ---------------------------------------------------------------------------
# upgrade_command — preserve extras + right mechanism per install type
# ---------------------------------------------------------------------------


def test_upgrade_command_uv_tool_preserves_extras_and_python():
    cmd = upgrade_command(
        ["graph", "mcp"], uv_tool=True, python="3.13", version="0.2.1"
    )
    assert cmd == [
        "uv",
        "tool",
        "install",
        "auditr[graph,mcp]==0.2.1",
        "--force",
        "--python",
        "3.13",
    ]


def test_upgrade_command_uv_tool_no_extras():
    cmd = upgrade_command([], uv_tool=True, python=None, version="0.2.1")
    assert cmd == ["uv", "tool", "install", "auditr==0.2.1", "--force"]


def test_upgrade_command_pip_pins_extras(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: MagicMock())
    cmd = upgrade_command(["mcp"], uv_tool=False, python=None, version="0.2.1")
    assert "-m" in cmd and "pip" in cmd and "auditr[mcp]==0.2.1" in cmd


def test_upgrade_command_falls_back_to_uv_pip(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    cmd = upgrade_command([], uv_tool=False, python=None, version="0.2.1")
    assert cmd[0] == "uv" and "pip" in cmd and "auditr==0.2.1" in cmd


def test_upgrade_command_raises_when_neither_available(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="no pip/uv found"):
        upgrade_command([], uv_tool=False, python=None, version="0.2.1")


# ---------------------------------------------------------------------------
# install_context — detect uv tool (receipt) vs pip/venv
# ---------------------------------------------------------------------------


def test_install_context_reads_uv_tool_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "uv-receipt.toml"
    receipt.write_text(
        "[tool]\n"
        'requirements = [{ name = "auditr", extras = ["mcp", "graph"] }]\n'
        'python = "3.13"\n'
    )
    monkeypatch.setattr("auditor.cli.self_update._uv_tool_receipt", lambda: receipt)
    ctx = install_context()
    assert ctx == InstallContext(uv_tool=True, python="3.13", extras=("mcp", "graph"))


def test_install_context_falls_back_to_env_inference(monkeypatch):
    monkeypatch.setattr("auditor.cli.self_update._uv_tool_receipt", lambda: None)
    monkeypatch.setattr(
        "auditor.cli.self_update._installed_extras_from_env", lambda: ("mcp",)
    )
    ctx = install_context()
    assert ctx == InstallContext(uv_tool=False, python=None, extras=("mcp",))


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
    captured: list[list[str]] = []

    monkeypatch.setattr("auditor.cli.self_update.installed_version", lambda: "0.2.0")
    monkeypatch.setattr(
        "auditor.cli.self_update.fetch_pypi",
        lambda **kw: {
            "info_version": "0.2.1",
            "releases": _RELEASES,
            "provides_extra": ["mcp", "graph"],
        },
    )
    monkeypatch.setattr(
        "auditor.cli.self_update.install_context",
        lambda: InstallContext(uv_tool=True, python="3.13", extras=("mcp", "graph")),
    )
    monkeypatch.setattr(
        "auditor.cli.self_update.subprocess.Popen",
        lambda cmd, **kw: (
            captured.append(cmd)
            or SimpleNamespace(returncode=0, poll=lambda: 0, wait=lambda: 0)
        ),
    )

    result = invoke("self", "update", "--yes")

    # real upgrade_command runs: uv-tool install pinned to latest, extras preserved
    assert captured == [
        [
            "uv",
            "tool",
            "install",
            "auditr[graph,mcp]==0.2.1",
            "--force",
            "--python",
            "3.13",
        ]
    ]
    assert "upgraded" in result.output


def test_update_yes_warns_and_drops_removed_extra(monkeypatch):
    """If an originally-installed extra is gone from the target release, it's dropped from the
    upgrade (not passed to a doomed install) and the user is told."""
    captured: list[list[str]] = []

    monkeypatch.setattr("auditor.cli.self_update.installed_version", lambda: "0.2.0")
    monkeypatch.setattr(
        "auditor.cli.self_update.fetch_pypi",
        lambda **kw: {
            "info_version": "0.2.1",
            "releases": _RELEASES,
            "provides_extra": ["mcp"],  # 'graph' removed upstream
        },
    )
    monkeypatch.setattr(
        "auditor.cli.self_update.install_context",
        lambda: InstallContext(uv_tool=True, python=None, extras=("mcp", "graph")),
    )
    monkeypatch.setattr(
        "auditor.cli.self_update.subprocess.Popen",
        lambda cmd, **kw: (
            captured.append(cmd)
            or SimpleNamespace(returncode=0, poll=lambda: 0, wait=lambda: 0)
        ),
    )

    result = invoke("self", "update", "--yes")

    assert captured == [
        ["uv", "tool", "install", "auditr[mcp]==0.2.1", "--force"]
    ]  # graph dropped
    assert "graph" in result.output  # user warned about the dropped extra
