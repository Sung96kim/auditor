"""The one SDK-bound module, checked where the extra is installed and skipped everywhere else.

CI never installs `observer-claude`, so this file is the only place the real `ClaudeAgentOptions`
shape and the real error classes are pinned.
"""

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import (  # noqa: E402  (only importable past the skip above)
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    ResultError,
)
from mcp.types import ListToolsRequest  # noqa: E402

from auditor.graph.refine.prompts import (  # noqa: E402
    ALLOWED_TOOLS,
    GRAPH_SERVER,
    GRAPH_TOOLS,
    OUTPUT_FORMAT,
    SYSTEM_PROMPT,
)
from auditor.graph.refine.sdk_client import (  # noqa: E402
    error_kind,
    sdk_options,
)
from auditor.graph.refine.sdk_runner import (  # noqa: E402
    BoundTools,
    SdkErrorKind,
    SdkOptions,
    claude_on_path,
)

PINNED = SdkOptions(
    model="haiku",
    cwd=Path("/tmp/repo"),
    cli_path=Path("/usr/local/bin/claude"),
    system_prompt=SYSTEM_PROMPT,
    max_turns=12,
    max_budget_usd=0.10,
)


@pytest.fixture
def built(refine_service):
    tools = BoundTools(service=refine_service, run_id="run-1")
    return sdk_options(PINNED, tools)


def test_the_options_are_the_isolated_ones_the_spike_measured(built):
    assert built.tools == ["Read", "Grep", "Glob"]
    assert built.allowed_tools == list(ALLOWED_TOOLS)
    assert built.setting_sources == []
    assert built.skills is None
    assert built.strict_mcp_config is True
    assert built.permission_mode == "dontAsk"
    assert built.effort == "low"
    assert built.output_format == OUTPUT_FORMAT


def test_the_run_specific_options_come_from_the_sdk_options(built):
    assert built.model == "haiku"
    assert (built.max_turns, built.max_budget_usd) == (12, 0.10)
    assert built.cwd == PINNED.cwd
    assert built.cli_path == PINNED.cli_path
    assert built.system_prompt == SYSTEM_PROMPT


def _registered_tools(built) -> tuple:
    """What the built in-process server really answers `tools/list` with."""
    server = built.mcp_servers[GRAPH_SERVER]["instance"]
    handler = server.request_handlers[ListToolsRequest]
    listed = asyncio.run(handler(ListToolsRequest(method="tools/list")))
    return tuple(listed.root.tools)


def test_the_in_process_server_carries_exactly_the_two_bound_tools(built):
    """The SDK-free half pins the table; this pins that the SDK registered that same table."""
    server = built.mcp_servers[GRAPH_SERVER]
    assert (server["type"], server["name"]) == ("sdk", GRAPH_SERVER)
    listed = _registered_tools(built)
    assert tuple(t.name for t in listed) == GRAPH_TOOLS
    assert all(t.description for t in listed)


def test_the_trace_hook_is_registered_for_every_tool(built):
    (matcher,) = built.hooks["PostToolUse"]
    assert matcher.matcher is None
    assert len(matcher.hooks) == 1


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (CLINotFoundError("nope"), SdkErrorKind.NOT_FOUND),
        (CLIConnectionError("nope"), SdkErrorKind.CONNECTION),
        (ResultError("nope"), SdkErrorKind.RESULT),
        (ProcessError("nope"), SdkErrorKind.PROCESS),
        (CLIJSONDecodeError("{", ValueError("x")), SdkErrorKind.DECODE),
    ],
)
def test_every_sdk_error_maps_to_a_kind_this_repo_can_name(exc, kind):
    assert error_kind(exc) is kind


def test_something_that_is_not_an_sdk_error_maps_to_nothing():
    assert error_kind(RuntimeError("elsewhere")) is None


def test_the_cli_on_path_is_a_real_file_or_nothing():
    found = claude_on_path()
    assert found is None or found.is_file()
