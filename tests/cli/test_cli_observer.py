"""The second lazy mount: full help without the daemon, real verbs on dispatch (P18)."""

import json

import pytest
from _support import invoke

import auditr_observer
from auditor.cli import observer as cli_observer
from auditor.cli.lazy import LazyObserverGroup, lazy_observer_app
from auditor.observer.daemon import DaemonRecord
from auditor.observer.payloads import DaemonStatus

#: the observer mount's own pin. `_SUBCOMMANDS` is the graph sub-app's list and must not grow.
_OBSERVER_SUBCOMMANDS = ("start", "stop", "status", "open", "ensure")


def test_the_root_help_lists_the_observer_mount():
    result = invoke("--help")
    assert result.exit_code == 0
    assert "observer" in result.stdout


@pytest.mark.parametrize("name", _OBSERVER_SUBCOMMANDS)
def test_every_observer_verb_is_reachable_through_the_mount(name):
    """Spec 12.2's five verbs; `auditr-observer` declares the same set plus `hook`."""
    result = invoke("observer", name, "--help")
    assert result.exit_code == 0


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("start", "did not start"),
        ("stop", "not running"),
        ("status", "not running"),
        ("open", "not running"),
        ("ensure", "did not start"),
    ],
)
def test_every_verb_answers_a_daemon_status_with_no_daemon_running(
    name, action, tmp_path, monkeypatch
):
    """Five verbs, one shape: with nothing running each still answers, and none exits non-zero.

    The action string is asserted exactly, because "started" and "did not start" are the same
    truthy value to a bare `assert payload.action`.

    `start` and `ensure` really do launch a daemon, so the spawn is stubbed: a unit test may not
    leave a background process behind.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    monkeypatch.setenv(
        "AUDITOR_USER_OBSERVER__SCHEDULING__START_TIMEOUT_SECONDS", "0.05"
    )
    monkeypatch.setattr(cli_observer, "detach", lambda argv, log: 0)
    result = invoke("observer", name, "--json")
    assert result.exit_code == 0
    payload = DaemonStatus.model_validate(json.loads(result.stdout))
    assert payload.running is False
    assert payload.home == str(tmp_path)
    assert payload.action == action


def test_the_mount_adopts_the_sub_app_only_when_a_verb_is_dispatched():
    group = LazyObserverGroup(name="observer")
    assert group.commands == {}
    assert set(group.list_commands(None)) == set(_OBSERVER_SUBCOMMANDS)


def test_the_mount_is_declared_but_not_resolved_at_import():
    assert LazyObserverGroup.module == "auditor.cli.observer"
    assert LazyObserverGroup.attribute == "observer_app"
    assert lazy_observer_app.registered_commands == []


def test_the_console_script_and_the_mount_declare_the_same_verbs():
    """One command surface with two front doors; a verb added to one has to reach the other."""
    assert set(auditr_observer._LIFECYCLE) == set(_OBSERVER_SUBCOMMANDS)


@pytest.mark.parametrize("name", _OBSERVER_SUBCOMMANDS)
def test_the_kill_switch_makes_every_mount_verb_a_no_op(name, tmp_path, monkeypatch):
    """Spec 8.1 and 14 disable everything, and P19 makes the two front doors one surface.

    Regression: only `start` and `ensure` read the switch, so `auditr observer status` still
    reported a live daemon while `auditr-observer status` refused to look at one.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    monkeypatch.setenv("AUDITOR_OBSERVER", "0")
    monkeypatch.setattr(
        cli_observer,
        "_running",
        lambda: pytest.fail("the switch is off; nothing may be probed"),
    )
    result = invoke("observer", name, "--json")
    assert result.exit_code == 0
    payload = DaemonStatus.model_validate(json.loads(result.stdout))
    assert payload.running is False
    assert payload.action == "disabled by AUDITOR_OBSERVER=0"


def test_the_human_render_names_the_action_and_the_page(tmp_path, monkeypatch):
    """Every verb test passes `--json`, so the rendered line nobody asserted could be gutted."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    monkeypatch.setattr(
        cli_observer,
        "_running",
        lambda: DaemonRecord(
            pid=4242, port=7682, home=str(tmp_path), version="0.10.5", compat=1
        ),
    )
    result = invoke("observer", "status")
    assert result.exit_code == 0
    assert "running" in result.stdout
    assert "7682" in result.stdout
    assert "http://127.0.0.1:7682/" in result.stdout


def test_foreground_and_json_are_refused_together(tmp_path, monkeypatch):
    """`--foreground` becomes the daemon and returns before any payload is rendered.

    Without the refusal this reaches `serve()`, which takes the home's lock and binds a port.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    result = invoke("observer", "start", "--foreground", "--json")
    assert result.exit_code == 2  # a usage error, before anything is started
    assert not (tmp_path / "observer" / "daemon.json").exists()
