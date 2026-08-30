"""The second lazy mount: full help without the daemon, real verbs on dispatch (P18)."""

import json

import pytest
from _support import invoke

import auditr_observer
from auditor.cli import observer as cli_observer
from auditor.cli.lazy import LazyObserverGroup, lazy_observer_app
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


@pytest.mark.parametrize("name", _OBSERVER_SUBCOMMANDS)
def test_every_verb_answers_a_daemon_status_with_no_daemon_running(
    name, tmp_path, monkeypatch
):
    """Five verbs, one shape: with nothing running each still answers, and none exits non-zero.

    `start` and `ensure` really do launch a daemon, so the spawn is stubbed: a unit test may not
    leave a background process behind.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    monkeypatch.setattr(cli_observer, "detach", lambda argv, log: 0)
    monkeypatch.setattr(cli_observer, "_START_TIMEOUT", 0.05)
    result = invoke("observer", name, "--json")
    assert result.exit_code == 0
    payload = DaemonStatus.model_validate(json.loads(result.stdout))
    assert payload.running is False
    assert payload.home == str(tmp_path)
    assert payload.action


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
