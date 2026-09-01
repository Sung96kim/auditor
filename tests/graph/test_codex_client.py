"""The one Codex-bound module, checked where the extra is installed and skipped everywhere else.

CI never installs `observer-codex`, so this file is the only place the real `TurnResult`,
`Sandbox` and `ApprovalMode` shapes are pinned.
"""

from pathlib import Path

import pytest

pytest.importorskip("openai_codex")

from codex_cli_bin import bundled_path_dir  # noqa: E402
from openai_codex import ApprovalMode, Sandbox  # noqa: E402
from openai_codex._run import TurnResult  # noqa: E402
from openai_codex.generated.v2_all import TurnStatus  # noqa: E402

from auditor.graph.refine.codex_client import (  # noqa: E402
    MCP_STATUS,
    RATE_LIMITS,
    _failed,
    codex_config,
)
from auditor.graph.refine.codex_home import BEARER_ENV  # noqa: E402
from auditor.graph.refine.codex_runner import (  # noqa: E402
    APPROVAL_MODE,
    SANDBOX,
    CodexOptions,
    from_turn,
)
from auditor.graph.refine.models import RunStatus  # noqa: E402
from auditor.graph.refine.prompts import RUN_ANSWER_SCHEMA, SYSTEM_PROMPT  # noqa: E402

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


def test_the_two_wrapper_less_rpcs_are_spelled_the_way_the_schema_names_them():
    assert (RATE_LIMITS, MCP_STATUS) == (
        "account/rateLimits/read",
        "mcpServerStatus/list",
    )
