"""The one Codex-bound module, checked where the extra is installed and skipped everywhere else.

This is the only place the real `TurnStatus`, `TurnResult`, `Sandbox` and `ApprovalMode` shapes
are pinned, so CI's `codex-shapes` job installs the extra and runs this file and nothing else.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("openai_codex")

from codex_cli_bin import bundled_path_dir  # noqa: E402
from openai_codex import ApprovalMode, AsyncCodex, Sandbox  # noqa: E402
from openai_codex._run import TurnResult  # noqa: E402
from openai_codex.generated.v2_all import TurnStatus  # noqa: E402

from auditor.graph.refine.codex_client import (  # noqa: E402
    MCP_STATUS,
    RATE_LIMITS,
    CodexClient,
    _failed,
    codex_config,
)
from auditor.graph.refine.codex_home import BEARER_ENV  # noqa: E402
from auditor.graph.refine.codex_runner import (  # noqa: E402
    APPROVAL_MODE,
    SANDBOX,
    CodexClientError,
    CodexOptions,
    from_turn,
)
from auditor.graph.refine.models import RunStatus  # noqa: E402
from auditor.graph.refine.prompts import RUN_ANSWER_SCHEMA, SYSTEM_PROMPT  # noqa: E402

ANSWER = {"summary": "one edge", "proposed": 1, "stopped_because": "done"}

OPTIONS = CodexOptions(
    model="gpt-5.1-codex",
    cwd=Path("/tmp/repo"),
    home=Path("/tmp/private-home"),
    auth=Path("/tmp/private-home/auth.json"),
    system_prompt=SYSTEM_PROMPT,
    output_schema=dict(RUN_ANSWER_SCHEMA),
    max_budget_usd=0.25,
)


def test_the_names_this_repo_pins_are_real_sdk_members():
    """A rename in the SDK is a failure here, not a `KeyError` on the first real run."""
    assert Sandbox[SANDBOX] is Sandbox.read_only
    assert ApprovalMode[APPROVAL_MODE] is ApprovalMode.deny_all


def test_the_private_home_and_the_token_reach_the_process_env():
    config = codex_config(OPTIONS, "tok-1")
    assert config.env["CODEX_HOME"] == "/tmp/private-home"
    assert config.env[BEARER_ENV] == "tok-1"
    assert config.cwd == "/tmp/repo"


def test_the_bundled_binary_is_the_one_that_is_run():
    """PATH's `codex` and the SDK's bundle drift by whole minors, and the bindings follow the
    bundle."""
    assert "codex_cli_bin" in str(codex_config(OPTIONS, "t").codex_bin)


def test_naming_the_binary_keeps_the_bundled_ripgrep_on_the_path():
    """The SDK prepends its tool dir only when it resolves the binary itself, and this does not."""
    bundled = bundled_path_dir()
    assert bundled is not None and (bundled / "rg").exists()
    assert codex_config(OPTIONS, "t").env["PATH"].split(":")[0] == str(bundled)


def test_a_raised_turn_becomes_the_result_the_sdk_never_returns():
    """`_raise_for_failed_turn` raises before a `TurnResult` reaches the caller (drift D1)."""
    turn = _failed(RuntimeError("turn completed event not received"))
    assert isinstance(turn, TurnResult)
    assert turn.status is TurnStatus.failed
    assert turn.usage is None
    ended = from_turn(turn, options=OPTIONS, prices={}, thread_id=None)
    assert ended.status is RunStatus.FAILED
    assert "turn completed event not received" in (ended.error or "")


def test_the_private_request_surface_the_two_rpcs_ride_on_is_still_there():
    """L4: `AsyncCodex._client.request` is SDK-private, and a minor bump can take it away.

    This job is the drift watch: nothing else in the suite sees the real client at all.
    """
    assert callable(AsyncCodex(codex_config(OPTIONS, "t"))._client.request)


def test_the_two_wrapper_less_rpcs_are_spelled_the_way_the_schema_names_them():
    assert (RATE_LIMITS, MCP_STATUS) == (
        "account/rateLimits/read",
        "mcpServerStatus/list",
    )


def _turn(status: TurnStatus, *, answer: bool = True) -> TurnResult:
    """One real `TurnResult` in the shape `openai_codex._run` hands the runner."""
    return TurnResult(
        id="t",
        status=status,
        error=None,
        started_at=None,
        completed_at=None,
        duration_ms=None,
        final_response=json.dumps(ANSWER) if answer else None,
        items=[],
        usage=None,
    )


def test_a_completed_turn_with_the_real_enum_succeeds():
    """C1: `TurnStatus` is a plain `Enum`, so `str()` on it is 'TurnStatus.completed'."""
    ended = from_turn(
        _turn(TurnStatus.completed), options=OPTIONS, prices={}, thread_id=None
    )
    assert ended.status is RunStatus.SUCCEEDED
    assert ended.summary == "one edge"


def test_an_interrupted_turn_with_the_real_enum_is_aborted():
    """The same bug from the other side: `STOPPED_STATUSES` never matched the enum's repr."""
    ended = from_turn(
        _turn(TurnStatus.interrupted, answer=False),
        options=OPTIONS,
        prices={},
        thread_id=None,
    )
    assert ended.status is RunStatus.ABORTED


def test_the_status_enum_is_not_a_string_mixin():
    """If the SDK ever makes it one, the unwrap stops being load bearing and this says so."""
    assert not isinstance(TurnStatus.completed, str)
    assert str(TurnStatus.completed) != TurnStatus.completed.value


def test_a_session_used_before_it_was_entered_refuses_rather_than_raising_attributeerror():
    """`self._codex` is `AsyncCodex | None`, and nothing typechecks this repo (L5)."""
    client = CodexClient(OPTIONS, tools=None)
    with pytest.raises(CodexClientError):
        client._opened()
