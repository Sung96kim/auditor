"""Which runner a request resolves to, and what it says when none can drive it."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from graph._support import Init, Result, fake_factory, init_data

from auditor.cli.helpers import load_settings, load_user, open_index
from auditor.graph.refine import drive
from auditor.graph.refine.models import RunnerKind, RunStatus
from auditor.graph.refine.runner import FakeRunner, RefinementJob, RunnerUnavailable
from auditor.graph.refine.sdk_runner import SdkRunner
from auditor.user_settings import RunnerConfig

ANSWER = {"summary": "one edge", "proposed": 1, "stopped_because": "done"}

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


async def test_drive_forwards_an_injected_client_factory_to_the_real_runner(
    refine_repo, monkeypatch
):
    """Without this seam a test cannot reach `SdkRunner` at all, and has to swap a whole runner
    class into the registry to get a scripted run through either surface."""
    monkeypatch.setattr(drive, "SDK_AVAILABLE", True)
    monkeypatch.setattr(drive, "auth_hinted", lambda *a, **k: True)
    settings, user = load_settings(refine_repo), load_user(refine_repo)
    messages = [Init(data=init_data()), Result(structured_output=ANSWER)]
    async with await open_index(refine_repo) as index:
        payload = await drive.refine(
            index,
            refine_repo,
            settings,
            user,
            job=RefinementJob(),
            client_factory=fake_factory(messages),
        )
    assert payload.run.status is RunStatus.SUCCEEDED
    assert payload.run.summary == "one edge"
    assert payload.choice is CODE.CLAUDE


#: everything `sdk_client.py` imports from the SDK, so a stub can drop exactly one of them
_SDK_EXPORTS = (
    "ClaudeAgentOptions",
    "ClaudeSDKClient",
    "CLIConnectionError",
    "CLIJSONDecodeError",
    "CLINotFoundError",
    "HookMatcher",
    "ProcessError",
    "ResultError",
    "create_sdk_mcp_server",
    "tool",
)
_IMPORT_DRIVE = (
    "import auditor.graph.refine.drive as d\nprint('SDK_AVAILABLE', d.SDK_AVAILABLE)\n"
)


def _stub_sdk(root: Path, *, missing: str | None) -> Path:
    """A `claude_agent_sdk` package on the path exporting everything but ``missing``."""
    package = root / "claude_agent_sdk"
    package.mkdir()
    names = [n for n in _SDK_EXPORTS if n != missing]
    body = "\n".join(f"{name} = object()" for name in names)
    (package / "__init__.py").write_text(f"{body}\n", encoding="utf-8")
    return root


def _import_drive(path: Path | None) -> subprocess.CompletedProcess[str]:
    """Import `drive` in a fresh interpreter, optionally with a stub SDK ahead of it."""
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=f"{path}:{root}" if path else str(root))
    return subprocess.run(
        [sys.executable, "-c", _IMPORT_DRIVE],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
    )


def test_an_sdk_whose_api_moved_is_not_reported_as_a_missing_extra(tmp_path):
    """CPython names the package for a failed `from pkg import Name`, so this failure and an
    absent extra arrive identically; telling the user to install what they have loses the real
    error, and the extra pins a range this can drift inside."""
    result = _import_drive(_stub_sdk(tmp_path, missing="create_sdk_mcp_server"))
    assert result.returncode != 0
    assert "cannot import name 'create_sdk_mcp_server'" in result.stderr


def test_an_sdk_that_is_really_absent_is_reported_as_a_missing_extra(tmp_path):
    """The branch this guard exists for: nothing on the path, no error, the runner unavailable."""
    result = _import_drive(None)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SDK_AVAILABLE False"


def test_a_complete_sdk_on_the_path_is_imported(tmp_path):
    result = _import_drive(_stub_sdk(tmp_path, missing=None))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SDK_AVAILABLE True"
