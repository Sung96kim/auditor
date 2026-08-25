"""Which runner a request resolves to, and what it says when none can drive it."""

import pytest

from auditor.graph.refine import drive
from auditor.graph.refine.models import RunnerKind
from auditor.graph.refine.runner import FakeRunner, RunnerUnavailable
from auditor.graph.refine.sdk_runner import SdkRunner
from auditor.user_settings import RunnerConfig

CODE = drive.RunnerChoiceCode


@pytest.mark.parametrize(
    ("agent", "sdk", "hint", "code"),
    [
        ("auto", True, True, CODE.CLAUDE),
        ("auto", True, False, CODE.PAUSED_AUTH),
        ("auto", False, True, CODE.UNAVAILABLE_SDK),
        ("auto", False, False, CODE.UNAVAILABLE_SDK),
        ("claude", True, True, CODE.CLAUDE),
        ("claude", True, False, CODE.PAUSED_AUTH),
        ("claude", False, True, CODE.UNAVAILABLE_SDK),
        ("codex", True, True, CODE.UNAVAILABLE_CODEX),
        ("codex", False, False, CODE.UNAVAILABLE_CODEX),
    ],
)
def test_the_choice_matrix(agent, sdk, hint, code):
    choice = drive.select_runner(
        RunnerConfig(agent=agent), sdk_available=sdk, auth_hint=hint
    )
    assert choice.code is code
    assert choice.kind is (RunnerKind.CLAUDE if code is CODE.CLAUDE else None)


@pytest.mark.parametrize(
    ("code", "named"),
    [
        (CODE.UNAVAILABLE_SDK, "observer-claude"),
        (CODE.PAUSED_AUTH, "log in"),
        (CODE.UNAVAILABLE_CODEX, "S12"),
    ],
)
def test_every_refusal_says_what_to_do_about_it(code, named):
    """The code is what the wire carries; the detail is the sentence a human acts on."""
    by_code = {
        CODE.UNAVAILABLE_SDK: dict(agent="claude", sdk_available=False, auth_hint=True),
        CODE.PAUSED_AUTH: dict(agent="claude", sdk_available=True, auth_hint=False),
        CODE.UNAVAILABLE_CODEX: dict(agent="codex", sdk_available=True, auth_hint=True),
    }[code]
    agent = by_code.pop("agent")
    choice = drive.select_runner(RunnerConfig(agent=agent), **by_code)
    assert choice.code is code
    assert named in choice.detail


def test_a_requested_runner_overrides_the_configured_one():
    config = RunnerConfig(agent="claude")
    choice = drive.select_runner(
        config, requested="codex", sdk_available=True, auth_hint=True
    )
    assert choice.code is CODE.UNAVAILABLE_CODEX


def test_the_injected_flags_beat_the_module_level_ones(monkeypatch):
    """A default argument would bind the flag by value at import and *call* `auth_hinted` once,
    so every monkeypatch in these tests would be a no-op."""
    monkeypatch.setattr(drive, "SDK_AVAILABLE", True)
    monkeypatch.setattr(drive, "auth_hinted", lambda *a, **k: True)
    assert drive.select_runner(RunnerConfig()).code is CODE.CLAUDE
    assert (
        drive.select_runner(RunnerConfig(), sdk_available=False).code
        is CODE.UNAVAILABLE_SDK
    )
    monkeypatch.setattr(drive, "SDK_AVAILABLE", False)
    assert drive.select_runner(RunnerConfig()).code is CODE.UNAVAILABLE_SDK


@pytest.mark.parametrize(
    "env",
    [{"ANTHROPIC_API_KEY": "k"}, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}],
)
def test_an_environment_credential_is_a_hint(env, tmp_path):
    assert drive.auth_hinted(env=env, home=tmp_path) is True


def test_a_stored_credential_file_is_a_hint(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / ".credentials.json").write_text("{}")
    assert drive.auth_hinted(env={}, home=tmp_path) is True


def test_no_signal_at_all_is_no_hint(tmp_path):
    assert drive.auth_hinted(env={}, home=tmp_path) is False


def test_the_registry_holds_both_runners():
    assert drive.RUNNERS[RunnerKind.FAKE] is FakeRunner
    assert drive.RUNNERS[RunnerKind.CLAUDE] is SdkRunner


def test_building_a_fake_runner_needs_no_client(refine_service):
    runner = drive.build_runner(RunnerKind.FAKE, refine_service)
    assert isinstance(runner, FakeRunner)
    assert runner.client_factory is None


def test_building_a_claude_runner_without_the_extra_names_it(
    refine_service, monkeypatch
):
    monkeypatch.setattr(drive, "SDK_AVAILABLE", False)
    with pytest.raises(RunnerUnavailable, match="observer-claude"):
        drive.build_runner(RunnerKind.CLAUDE, refine_service)


def test_an_injected_factory_is_used_as_it_stands(refine_service, monkeypatch):
    monkeypatch.setattr(drive, "SDK_AVAILABLE", False)
    factory = object()
    runner = drive.build_runner(
        RunnerKind.CLAUDE, refine_service, client_factory=factory
    )
    assert isinstance(runner, SdkRunner)
    assert runner.client_factory is factory
