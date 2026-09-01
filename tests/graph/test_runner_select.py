"""Spec 9.3's choice ladder: which runner drives a request, and what it says when none can."""

import importlib
import importlib.util
import sys

import pytest

from auditor.graph.refine import drive
from auditor.graph.refine.models import RunnerKind
from auditor.user_settings import RunnerConfig

CODE = drive.RunnerChoiceCode
#: every arm of the ladder, as `(agent, sdk, claude auth, codex sdk, codex auth) -> code`
LADDER = [
    ("auto", True, True, True, True, CODE.CLAUDE),
    ("auto", True, True, False, False, CODE.CLAUDE),
    ("auto", False, False, True, True, CODE.CODEX),
    ("auto", True, False, True, True, CODE.CODEX),
    ("auto", True, False, True, False, CODE.PAUSED_AUTH),
    ("auto", False, False, True, False, CODE.PAUSED_AUTH),
    ("auto", False, False, False, False, CODE.UNAVAILABLE_NONE),
    ("claude", True, True, True, True, CODE.CLAUDE),
    ("claude", False, True, True, True, CODE.UNAVAILABLE_SDK),
    ("claude", True, False, True, True, CODE.PAUSED_AUTH),
    ("codex", True, True, True, True, CODE.CODEX),
    ("codex", True, True, False, True, CODE.UNAVAILABLE_CODEX),
    ("codex", True, True, True, False, CODE.PAUSED_AUTH),
]


def choose(agent, sdk, hint, codex, codex_hint, requested=None):
    return drive.select_runner(
        RunnerConfig(agent=agent),
        requested=requested,
        sdk_available=sdk,
        auth_hint=hint,
        codex_available=codex,
        codex_auth_hint=codex_hint,
    )


@pytest.mark.parametrize(
    ("agent", "sdk", "hint", "codex", "codex_hint", "code"),
    LADDER,
    ids=[f"{row[0]}-{row[5].value}-{i}" for i, row in enumerate(LADDER)],
)
def test_the_choice_matrix(agent, sdk, hint, codex, codex_hint, code):
    choice = choose(agent, sdk, hint, codex, codex_hint)
    assert choice.code is code


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (CODE.CLAUDE, RunnerKind.CLAUDE),
        (CODE.CODEX, RunnerKind.CODEX),
        (CODE.PAUSED_AUTH, None),
        (CODE.UNAVAILABLE_SDK, None),
        (CODE.UNAVAILABLE_CODEX, None),
        (CODE.UNAVAILABLE_NONE, None),
    ],
)
def test_every_code_names_its_runner_or_names_none(code, kind):
    """`RunnerChoice.kind` reads the runner off the code, so no pair can disagree."""
    assert drive.RunnerChoice(code=code).kind is kind


def test_auto_prefers_claude_even_when_both_are_ready():
    assert choose("auto", True, True, True, True).kind is RunnerKind.CLAUDE


def test_auto_falling_to_codex_says_the_cost_model_changed():
    """Falling from measured dollars to a derived estimate is a fact the caller has to see."""
    choice = choose("auto", False, False, True, True)
    assert choice.kind is RunnerKind.CODEX
    assert "costs are estimated" in choice.detail


def test_an_explicit_codex_never_falls_back_to_claude():
    assert choose("codex", True, True, False, False).code is CODE.UNAVAILABLE_CODEX


def test_a_requested_runner_overrides_the_configured_one():
    choice = choose("claude", True, True, True, True, requested="codex")
    assert choice.kind is RunnerKind.CODEX


@pytest.mark.parametrize(
    ("code", "named", "args"),
    [
        (CODE.UNAVAILABLE_SDK, "observer-claude", ("claude", False, True, True, True)),
        (CODE.UNAVAILABLE_CODEX, "observer-codex", ("codex", True, True, False, True)),
        (CODE.PAUSED_AUTH, "run `claude` once", ("claude", True, False, True, True)),
        (CODE.PAUSED_AUTH, "run `codex` once", ("codex", True, True, True, False)),
        (
            CODE.UNAVAILABLE_NONE,
            "auditr[observer]",
            ("auto", False, False, False, False),
        ),
    ],
)
def test_every_refusal_says_what_to_do_about_it(code, named, args):
    choice = choose(*args)
    assert choice.code is code
    assert named in choice.detail


def test_the_codex_runner_is_registered_under_its_own_kind():
    assert drive.RUNNERS[RunnerKind.CODEX].kind is RunnerKind.CODEX


@pytest.mark.parametrize("present", [True, False])
def test_availability_is_presence_and_never_an_eager_import(monkeypatch, present):
    """`import openai_codex` costs most of a second; `find_spec` answers without paying it.

    Reloaded under a patched `find_spec` so both arms run wherever the suite runs: CI installs
    no `observer-codex` and a developer machine may install it.
    """
    real = importlib.util.find_spec
    asked: list[str] = []

    def spec(name: str, *args: object, **kwargs: object) -> object | None:
        if name != "openai_codex":
            return real(name, *args, **kwargs)
        asked.append(name)
        return object() if present else None

    monkeypatch.setattr(importlib.util, "find_spec", spec)
    monkeypatch.delitem(sys.modules, "openai_codex", raising=False)
    try:
        reloaded = importlib.reload(drive)
        assert reloaded.CODEX_AVAILABLE is present
        assert asked == ["openai_codex"]
        assert "openai_codex" not in sys.modules
    finally:
        monkeypatch.undo()
        importlib.reload(drive)


def test_the_injected_flags_beat_the_module_level_ones(monkeypatch):
    monkeypatch.setattr(drive, "SDK_AVAILABLE", False)
    monkeypatch.setattr(drive, "CODEX_AVAILABLE", True)
    monkeypatch.setattr(drive, "auth_hinted", lambda *a, **k: False)
    monkeypatch.setattr(drive, "codex_auth_hinted", lambda *a, **k: True)
    assert drive.select_runner(RunnerConfig()).kind is RunnerKind.CODEX
    assert (
        drive.select_runner(RunnerConfig(), codex_available=False).code
        is CODE.UNAVAILABLE_NONE
    )
