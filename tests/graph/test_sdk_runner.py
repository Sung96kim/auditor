"""The Claude runner without the SDK: the init check, the message loop, and the outcome mapping."""

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from graph._support import (
    Assistant,
    FakeClient,
    Init,
    Limit,
    LimitInfo,
    Result,
    Tick,
    fake_factory,
    init_data,
)

from auditor.graph.model import EdgeKind
from auditor.graph.refine.models import (
    Proposal,
    RefinementKind,
    RefinementTarget,
    Run,
    RunStatus,
    RunUsage,
)
from auditor.graph.refine.prompts import (
    ALLOWED_TOOLS,
    GRAPH_TOOLS,
    OUTPUT_FORMAT,
    RUN_ANSWER_SCHEMA,
    SYSTEM_PROMPT,
)
from auditor.graph.refine.runner import RefinementJob, RunProduct
from auditor.graph.refine.sdk_runner import (
    ASSISTANT_ERRORS,
    EFFORT,
    PERMISSION_MODE,
    SETTING_SOURCES,
    STRICT_MCP_CONFIG,
    BoundTools,
    SdkClientError,
    SdkErrorKind,
    SdkOptions,
    SdkRunner,
    from_result,
    run_answer,
)
from auditor.graph.refine.service import RefinementService

GOOD = Proposal(
    kind=RefinementKind.ADD_EDGE,
    target=RefinementTarget(
        src="impl.py::Impl.run",
        dst="svc.py::load_user",
        edge_kind=EdgeKind.CALLS,
        name="load_user",
    ),
    reason="Impl.run calls load_user, which svc.py defines",
).model_dump()
ANSWER = {"summary": "one edge", "proposed": 1, "stopped_because": "done"}
MODEL_USAGE = {
    "claude-haiku-4-5-20251001": {
        "inputTokens": 100,
        "outputTokens": 20,
        "costUSD": 0.004,
    }
}
PINNED_OPTIONS = SdkOptions(
    model="haiku",
    cwd=Path("/tmp/repo"),
    cli_path=None,
    system_prompt=SYSTEM_PROMPT,
    max_turns=12,
    max_budget_usd=0.10,
)
PROPOSES = Assistant(tool_calls=(("mcp__graph__propose", GOOD),))
ANSWERS = Assistant(tool_calls=(("StructuredOutput", ANSWER),))
SUCCESS = Result(
    structured_output=ANSWER, model_usage=MODEL_USAGE, total_cost_usd=0.004, num_turns=3
)


def _runner(
    service: RefinementService,
    messages: Sequence[Any],
    seen: list[SdkOptions] | None = None,
    **kwargs: Any,
) -> SdkRunner:
    """A runner over a scripted client, with no managed-settings file in the way."""
    kwargs.setdefault("managed_settings", service.root / "no-such-settings.json")
    return SdkRunner(service, fake_factory(messages, seen), **kwargs)


async def _drive(
    service: RefinementService, messages: Sequence[Any], **kwargs: Any
) -> tuple[RunProduct, Run]:
    """One scripted run, and the row it left behind."""
    product = await _runner(service, messages, **kwargs).run(RefinementJob())
    (row,) = await service.index.runs.runs()
    return product, row


async def test_a_scripted_run_proposes_through_the_service_and_records_it(
    refine_service: RefinementService,
):
    _product, row = await _drive(
        refine_service, [Init(data=init_data()), PROPOSES, ANSWERS, SUCCESS]
    )
    assert row.status is RunStatus.SUCCEEDED
    assert row.summary == "one edge"
    assert row.usage == RunUsage(
        cost_usd=0.004,
        cost_estimated=False,
        input_tokens=100,
        output_tokens=20,
        num_turns=3,
    )
    assert [call.tool for call in row.tool_trace] == [
        "mcp__graph__propose",
        "StructuredOutput",
    ]
    assert row.sdk_session_id == "sdk-session"
    assert row.system_prompt_sha and row.prompt
    stored = await refine_service.index.refinements.of_run(row.run_id)
    assert [r.status.value for r in stored] == ["pending"]


async def test_the_options_handed_to_the_factory_are_the_pinned_ones(
    refine_service: RefinementService,
):
    """Invariant 4: what varies is a field, what cannot is a module constant beside it."""
    seen: list[SdkOptions] = []
    await _drive(refine_service, [Init(data=init_data()), SUCCESS], seen=seen)
    (options,) = seen
    assert options.tools == ("Read", "Grep", "Glob")
    assert options.model == refine_service.user.observer.runner.model
    assert options.max_turns == refine_service.user.observer.limits.max_turns
    assert (
        options.max_budget_usd
        == refine_service.user.observer.budget.max_budget_usd_per_run
    )
    assert options.system_prompt == SYSTEM_PROMPT
    assert options.cwd == refine_service.root


def test_the_options_that_cannot_vary_are_constants():
    assert (SETTING_SOURCES, STRICT_MCP_CONFIG) == ((), True)
    assert (PERMISSION_MODE, EFFORT) == ("dontAsk", "low")
    assert OUTPUT_FORMAT == {"type": "json_schema", "schema": RUN_ANSWER_SCHEMA}
    assert set(ALLOWED_TOOLS) == {
        "Read",
        "Grep",
        "Glob",
        "mcp__graph__propose",
        "mcp__graph__brief",
        "StructuredOutput",
    }


async def test_a_job_model_overrides_the_configured_one(
    refine_service: RefinementService,
):
    seen: list[SdkOptions] = []
    runner = _runner(refine_service, [Init(data=init_data()), SUCCESS], seen=seen)
    await runner.run(RefinementJob(model="sonnet"))
    assert seen[0].model == "sonnet"


async def test_the_init_check_accepts_the_dated_model_the_cli_reports(
    refine_service: RefinementService,
):
    """The spike measured `claude-haiku-4-5-20251001` for the option `haiku`, so a prefix test
    would refuse every real run."""
    _product, row = await _drive(
        refine_service,
        [Init(data=init_data(model="claude-haiku-4-5-20251001")), SUCCESS],
    )
    assert row.status is RunStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("spoiled", "named"),
    [
        ({"mcp_servers": [{"name": "other", "status": "connected"}]}, "other"),
        ({"mcp_servers": [{"name": "graph", "status": "failed"}]}, "not connected"),
        ({"plugins": [{"name": "auditor"}]}, "plugins"),
        ({"tools": ["Read", "Bash"]}, "['Bash']"),
        ({"permissionMode": "default"}, "permission mode"),
        ({"model": "claude-sonnet-4-5-20250929"}, "is not haiku"),
    ],
)
async def test_a_session_that_is_not_the_one_asked_for_is_refused(
    refine_service: RefinementService, spoiled, named
):
    """Invariant 4: the refusal names what it refused, because a surprise here is a fact about the
    CLI, not about this repo."""
    _product, row = await _drive(
        refine_service, [Init(data=init_data(**spoiled)), PROPOSES, SUCCESS]
    )
    assert (row.error or "").startswith("refused:")
    assert named in (row.error or "")
    assert row.status is RunStatus.ABORTED
    assert await refine_service.index.refinements.of_run(row.run_id) == []


async def test_a_result_before_any_init_is_a_failed_run(
    refine_service: RefinementService,
):
    _product, row = await _drive(refine_service, [SUCCESS])
    assert row.status is RunStatus.FAILED
    assert "before it started" in (row.error or "")


async def test_ticks_and_a_rate_limit_warning_before_init_are_skipped(
    refine_service: RefinementService,
):
    """The spike measured two SystemMessages and a RateLimitEvent before the init in one run."""
    _product, row = await _drive(
        refine_service,
        [
            Tick(),
            Limit(rate_limit_info=LimitInfo(status="allowed_warning")),
            Tick(),
            Init(data=init_data()),
            SUCCESS,
        ],
    )
    assert row.status is RunStatus.SUCCEEDED


async def test_an_authentication_failure_is_named_the_way_the_daemon_reads_it(
    refine_service: RefinementService,
):
    _product, row = await _drive(
        refine_service,
        [Init(data=init_data()), Assistant(error="authentication_failed"), SUCCESS],
    )
    assert (row.status, row.error) == (RunStatus.FAILED, "paused:auth")


async def test_another_assistant_error_is_reported_as_it_came(
    refine_service: RefinementService,
):
    _product, row = await _drive(
        refine_service, [Init(data=init_data()), Assistant(error="server_error")]
    )
    assert (row.status, row.error) == (RunStatus.FAILED, "server_error")


async def test_a_rejected_rate_limit_pauses_the_run(refine_service: RefinementService):
    _product, row = await _drive(
        refine_service,
        [
            Init(data=init_data()),
            Limit(rate_limit_info=LimitInfo(status="rejected", resets_at=1788159600)),
            SUCCESS,
        ],
    )
    assert row.status is RunStatus.ABORTED
    assert row.error == "paused:ratelimit until 1788159600"


async def test_a_budget_stop_keeps_its_cost_and_loses_its_staging(
    refine_service: RefinementService,
):
    """`abort` stores nothing, so a capped run discards every proposal it made (P12)."""
    capped = Result(
        subtype="error_max_budget_usd",
        num_turns=1,
        total_cost_usd=0.000964,
        model_usage=MODEL_USAGE,
        errors=("Reached maximum budget ($0.0001)",),
    )
    _product, row = await _drive(
        refine_service, [Init(data=init_data()), PROPOSES, capped]
    )
    assert row.status is RunStatus.ABORTED
    assert "Reached maximum budget" in (row.error or "")
    assert row.usage.cost_usd == pytest.approx(0.000964)
    assert await refine_service.index.refinements.of_run(row.run_id) == []


async def test_a_turn_cap_is_an_abort_too(refine_service: RefinementService):
    _product, row = await _drive(
        refine_service,
        [
            Init(data=init_data()),
            Result(subtype="error_max_turns", structured_output=None),
        ],
    )
    assert row.status is RunStatus.ABORTED


@pytest.mark.parametrize(
    "answer", [None, {"summary": "s", "proposed": 1, "stopped_because": "done", "x": 1}]
)
async def test_a_success_with_no_usable_answer_is_a_failed_run(
    refine_service: RefinementService, answer
):
    """A malformed output_format drops the flag silently and the run still "succeeds" (spike A.6),
    so the answer is what says it really finished."""
    _product, row = await _drive(
        refine_service, [Init(data=init_data()), Result(structured_output=answer)]
    )
    assert row.status is RunStatus.FAILED
    assert row.error == "no structured answer"


async def test_a_stream_that_never_answers_is_a_failed_run(
    refine_service: RefinementService,
):
    _product, row = await _drive(refine_service, [Init(data=init_data())])
    assert row.status is RunStatus.FAILED
    assert "without a result" in (row.error or "")


async def test_a_client_that_cannot_start_leaves_a_run_row_behind(
    refine_service: RefinementService,
):
    def explode(options: SdkOptions, tools: BoundTools) -> FakeClient:
        raise SdkClientError("Claude Code not found", kind=SdkErrorKind.NOT_FOUND)

    runner = SdkRunner(
        refine_service,
        explode,
        managed_settings=refine_service.root / "none.json",
    )
    await runner.run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert row.status is RunStatus.FAILED
    assert "Claude Code not found" in (row.error or "")


async def test_managed_settings_that_declare_hooks_refuse_the_run(
    refine_service: RefinementService, tmp_path
):
    """The one settings tier `setting_sources=[]` cannot reach, so it is read as a file (P3)."""
    settings = tmp_path / "managed-settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": []}}))
    runner = _runner(
        refine_service, [Init(data=init_data()), SUCCESS], managed_settings=settings
    )
    await runner.run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert row.status is RunStatus.ABORTED
    assert "declares hooks" in (row.error or "")


async def test_managed_settings_without_hooks_let_the_run_proceed(
    refine_service: RefinementService, tmp_path
):
    settings = tmp_path / "managed-settings.json"
    settings.write_text(json.dumps({"permissions": {}}))
    runner = _runner(
        refine_service, [Init(data=init_data()), SUCCESS], managed_settings=settings
    )
    await runner.run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert row.status is RunStatus.SUCCEEDED


async def test_tokens_come_from_the_usage_block_when_there_is_no_breakdown(
    refine_service: RefinementService,
):
    result = Result(
        structured_output=ANSWER,
        model_usage=None,
        usage={"input_tokens": 3959, "output_tokens": 171},
    )
    _product, row = await _drive(refine_service, [Init(data=init_data()), result])
    assert (row.usage.input_tokens, row.usage.output_tokens) == (3959, 171)


async def test_two_model_entries_are_summed(refine_service: RefinementService):
    result = Result(
        structured_output=ANSWER,
        model_usage={
            "a": {"inputTokens": 10, "outputTokens": 1},
            "b": {"inputTokens": 5, "outputTokens": 2},
        },
    )
    _product, row = await _drive(refine_service, [Init(data=init_data()), result])
    assert (row.usage.input_tokens, row.usage.output_tokens) == (15, 3)


async def test_the_bound_propose_tool_answers_with_the_verdict(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    tools = BoundTools(service=refine_service, run_id=run.run_id)
    answer = await tools.propose(GOOD)
    assert "is_error" not in answer
    assert json.loads(answer["content"][0]["text"])["outcome"] == "staged"


async def test_the_bound_propose_tool_reports_a_refusal_as_an_error(
    refine_service: RefinementService,
):
    tools = BoundTools(service=refine_service, run_id="no-such-run")
    answer = await tools.propose(GOOD)
    assert answer["is_error"] is True
    assert "not open in this process" in answer["content"][0]["text"]


async def test_the_bound_brief_tool_shows_the_verdicts_so_far(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    tools = BoundTools(service=refine_service, run_id=run.run_id)
    await tools.propose(GOOD)
    text = (await tools.brief({}))["content"][0]["text"]
    assert "Verdicts so far" in text and "staged" in text


async def test_the_hook_records_one_call_per_tool_use(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    tools = BoundTools(service=refine_service, run_id=run.run_id)
    await tools.record(
        {"tool_name": "Read", "tool_input": {"file_path": "a.py"}, "duration_ms": 5}
    )
    (call,) = tools.trace
    assert call.tool == "Read"
    assert call.detail.startswith("5 ms; ")
    assert "a.py" in call.detail


async def test_the_client_receives_the_rendered_brief_as_its_prompt(
    refine_service: RefinementService,
):
    seen: list[SdkOptions] = []
    clients: list[FakeClient] = []

    def factory(options: SdkOptions, tools: BoundTools) -> FakeClient:
        seen.append(options)
        client = FakeClient([Init(data=init_data()), SUCCESS], tools)
        clients.append(client)
        return client

    runner = SdkRunner(
        refine_service, factory, managed_settings=refine_service.root / "none.json"
    )
    product = await runner.run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert clients[0].prompt == product.brief.render()
    # the row and the client got the same text: two renders of one object prove neither
    assert row.prompt == clients[0].prompt


class _Cancelling:
    """A client whose stream is cancelled after the session was accepted."""

    def __init__(self, messages: list, tools: BoundTools) -> None:
        self.messages = messages
        self.tools = tools
        self.prompt: str | None = None

    async def __aenter__(self) -> "_Cancelling":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.prompt = prompt

    async def receive_response(self):
        yield Init(data=init_data())
        raise asyncio.CancelledError


async def test_a_cancelled_run_is_aborted_before_the_cancellation_goes_on(
    refine_service: RefinementService,
):
    """`CancelledError` is a `BaseException`, so `except Exception` never sees it and the row
    would stay `queued` holding a `max_open_runs` slot until the stranded sweep."""
    runner = SdkRunner(
        refine_service,
        lambda options, tools: _Cancelling([], tools),
        managed_settings=refine_service.root / "none.json",
    )
    with pytest.raises(asyncio.CancelledError):
        await runner.run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert (row.status, row.error) == (RunStatus.ABORTED, "cancelled")
    assert row.finished_at is not None
    assert refine_service.registry.open_runs == {}


@pytest.mark.parametrize(
    ("literal", "status", "reason"),
    [
        ("authentication_failed", RunStatus.FAILED, "paused:auth"),
        ("rate_limit", RunStatus.ABORTED, "paused:ratelimit"),
        ("billing_error", RunStatus.ABORTED, "paused:billing"),
        ("invalid_request", RunStatus.FAILED, "invalid_request"),
        ("server_error", RunStatus.FAILED, "server_error"),
        ("unknown", RunStatus.FAILED, "unknown"),
    ],
)
async def test_every_assistant_error_literal_maps_to_one_status_and_one_word(
    refine_service: RefinementService, literal, status, reason
):
    """S8's loop reads these words, so a rate limit must not mean one thing on the assistant path
    and another on the `RateLimitEvent` one."""
    _product, row = await _drive(
        refine_service, [Init(data=init_data()), Assistant(error=literal)]
    )
    assert (row.status, row.error) == (status, reason)


def test_the_error_table_covers_the_whole_sdk_literal():
    """`AssistantMessageError` is a closed `Literal`; a member with no row would fall through to
    a bare `failed` and lose the pause the daemon acts on."""
    assert set(ASSISTANT_ERRORS) == {
        "authentication_failed",
        "billing_error",
        "rate_limit",
        "invalid_request",
        "server_error",
        "unknown",
    }


async def test_a_success_that_carries_is_error_is_a_failed_run(
    refine_service: RefinementService,
):
    """The SDK documents `api_error_status` as set when `is_error` is true on a `success`, so a
    structured answer alongside it must not be committed."""
    broken = Result(structured_output=ANSWER, is_error=True, api_error_status=529)
    _product, row = await _drive(refine_service, [Init(data=init_data()), broken])
    assert row.status is RunStatus.FAILED
    assert "529" in (row.error or "")
    assert await refine_service.index.refinements.of_run(row.run_id) == []


async def test_cached_input_tokens_are_input_tokens(refine_service: RefinementService):
    """A run that read its context from cache was charged for it, and S7's eval reads the count."""
    result = Result(
        structured_output=ANSWER,
        model_usage={
            "claude-haiku-4-5-20251001": {
                "inputTokens": 100,
                "cacheCreationInputTokens": 30,
                "cacheReadInputTokens": 7,
                "outputTokens": 20,
            }
        },
    )
    _product, row = await _drive(refine_service, [Init(data=init_data()), result])
    assert (row.usage.input_tokens, row.usage.output_tokens) == (137, 20)


async def test_the_usage_block_counts_its_cache_fields_too(
    refine_service: RefinementService,
):
    result = Result(
        structured_output=ANSWER,
        model_usage=None,
        usage={
            "input_tokens": 3959,
            "cache_creation_input_tokens": 11,
            "cache_read_input_tokens": 2,
            "output_tokens": 171,
        },
    )
    _product, row = await _drive(refine_service, [Init(data=init_data()), result])
    assert (row.usage.input_tokens, row.usage.output_tokens) == (3972, 171)


@pytest.mark.parametrize(
    ("messages", "named"),
    [
        ([Init(data=init_data()), Assistant(error="server_error")], "server_error"),
        ([Init(data=init_data())], "without a result"),
        (
            [
                Init(data=init_data()),
                Limit(rate_limit_info=LimitInfo(status="rejected", resets_at=1)),
            ],
            "paused:ratelimit",
        ),
    ],
    ids=["assistant", "no-result", "rate-limit"],
)
async def test_a_run_that_stopped_before_its_result_keeps_its_session_and_marks_its_cost(
    refine_service: RefinementService, messages, named
):
    """An unknown cost is not a measured zero, and the session id is what a later reader resumes
    from: both are pinned only on the result path otherwise."""
    _product, row = await _drive(refine_service, messages)
    assert named in (row.error or "")
    assert row.usage.cost_estimated is True
    assert row.usage.cost_usd == 0.0
    assert row.sdk_session_id == "sdk-session"


async def test_managed_settings_that_cannot_be_read_refuse_the_run(
    refine_service: RefinementService, tmp_path
):
    """The one tier `setting_sources=[]` cannot switch off: a file we cannot read is not a file
    we may assume declares no hooks."""
    settings = tmp_path / "managed-settings.json"
    settings.write_text("{not json")
    runner = _runner(
        refine_service, [Init(data=init_data()), SUCCESS], managed_settings=settings
    )
    await runner.run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert row.status is RunStatus.ABORTED
    assert "cannot be read" in (row.error or "")


def test_an_init_with_no_mcp_servers_says_the_graph_server_is_missing():
    """ "unexpected mcp servers []" reads as "there were extra ones" when the problem is none."""
    refused = PINNED_OPTIONS.refusal(init_data(mcp_servers=[]))
    assert refused is not None
    assert "no mcp servers" in refused


@pytest.mark.parametrize(
    ("spoiled", "named"),
    [
        ({"mcp_servers": [{"name": "other", "status": "connected"}]}, "other"),
        ({"mcp_servers": [{"name": "graph", "status": "failed"}]}, "not connected"),
        ({"plugins": [{"name": "auditor"}]}, "plugins"),
        ({"tools": ["Read", "Bash"]}, "['Bash']"),
        ({"permissionMode": "default"}, "permission mode"),
        ({"model": "claude-sonnet-4-5-20250929"}, "is not haiku"),
    ],
)
def test_the_init_check_is_a_read_of_the_options_alone(spoiled, named):
    """Invariant 4 is a property of the options the CLI was asked for, so it is testable without
    a client, a run or a service."""
    refused = PINNED_OPTIONS.refusal(init_data(**spoiled))
    assert refused is not None and named in refused


def test_the_init_check_accepts_the_session_it_asked_for():
    assert PINNED_OPTIONS.refusal(init_data()) is None


def test_the_outcome_of_a_result_is_readable_without_a_client():
    """P8/P12's mapping is the contract, so it is pinned directly and not only through a fake."""
    outcome = from_result(SUCCESS, session_id="s-1", trace=())
    assert (outcome.status, outcome.summary) == (RunStatus.SUCCEEDED, "one edge")
    assert outcome.sdk_session_id == "s-1"
    assert outcome.usage.cost_estimated is False


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        (None, False),
        ({"summary": "s", "proposed": 1, "stopped_because": "done"}, True),
        ({"summary": "s", "proposed": 1, "stopped_because": "nope"}, False),
    ],
)
def test_only_an_answer_the_schema_accepts_is_an_answer(raw, kept):
    assert (run_answer(raw) is not None) is kept


def test_the_bound_table_is_the_one_the_prompt_names(refine_service: RefinementService):
    """Three modules used to have to agree on these two names; now one table carries them, and
    this is the pin that says so without the SDK installed."""
    tools = BoundTools(service=refine_service, run_id="run-1")
    table = tools.tools()
    assert tuple(t.name for t in table) == GRAPH_TOOLS
    assert tuple(t.handler.__name__ for t in table) == GRAPH_TOOLS
    assert {t.qualified for t in table} <= set(ALLOWED_TOOLS)
    assert all(t.description and t.input_schema for t in table)


async def test_a_refused_session_records_no_session_id(
    refine_service: RefinementService,
):
    """The refusal is of that session, so recording its id would say the run ran under it."""
    _product, row = await _drive(
        refine_service, [Init(data=init_data(plugins=[{"name": "x"}]))]
    )
    assert row.status is RunStatus.ABORTED
    assert row.sdk_session_id is None


async def test_a_scripted_call_to_a_tool_the_run_does_not_expose_is_an_error(
    refine_service: RefinementService,
):
    """The double resolves through the same table, so a renamed tool is caught here rather than
    silently answering `{}` and still leaving a trace entry."""
    _product, row = await _drive(
        refine_service,
        [
            Init(data=init_data()),
            Assistant(tool_calls=(("mcp__graph__nope", {}),)),
            SUCCESS,
        ],
    )
    assert row.status is RunStatus.FAILED
    assert "does not expose" in (row.error or "")
    assert row.tool_trace == ()
