"""The Claude runner without the SDK: the init check, the message loop, and the outcome mapping."""

import json

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
    RunStatus,
    RunUsage,
)
from auditor.graph.refine.prompts import (
    ALLOWED_TOOLS,
    OUTPUT_FORMAT,
    RUN_ANSWER_SCHEMA,
    SYSTEM_PROMPT,
)
from auditor.graph.refine.runner import RefinementJob
from auditor.graph.refine.sdk_runner import (
    EFFORT,
    PERMISSION_MODE,
    SETTING_SOURCES,
    STRICT_MCP_CONFIG,
    BoundTools,
    SdkClientError,
    SdkErrorKind,
    SdkRunner,
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
PROPOSES = Assistant(tool_calls=(("mcp__graph__propose", GOOD),))
ANSWERS = Assistant(tool_calls=(("StructuredOutput", ANSWER),))
SUCCESS = Result(
    structured_output=ANSWER, model_usage=MODEL_USAGE, total_cost_usd=0.004, num_turns=3
)


def _runner(service: RefinementService, messages, seen=None, **kwargs) -> SdkRunner:
    """A runner over a scripted client, with no managed-settings file in the way."""
    kwargs.setdefault("managed_settings", service.root / "no-such-settings.json")
    return SdkRunner(service, fake_factory(messages, seen), **kwargs)


async def _drive(service: RefinementService, messages, **kwargs):
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
    seen: list = []
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
    seen: list = []
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
    def explode(options, tools):
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
    seen: list = []
    clients: list[FakeClient] = []

    def factory(options, tools):
        seen.append(options)
        client = FakeClient([Init(data=init_data()), SUCCESS], tools)
        clients.append(client)
        return client

    runner = SdkRunner(
        refine_service, factory, managed_settings=refine_service.root / "none.json"
    )
    product = await runner.run(RefinementJob())
    assert clients[0].prompt == product.brief.render()
