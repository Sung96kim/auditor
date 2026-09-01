"""The Codex runner without the SDK: the option set, the turn mapping and the ceilings."""

import json
from pathlib import Path
from typing import Any

import pytest
from graph._support import (
    Breakdown,
    Command,
    McpCall,
    Rooted,
    Turn,
    TurnError,
    Usage,
    codex_factory,
)

from auditor.graph.refine.codex_runner import (
    APPROVAL_MODE,
    CALLS_PER_RUN,
    EFFORT,
    SANDBOX,
    CodexOptions,
    CodexRunner,
    RateLimit,
    codex_usage,
    from_turn,
    managed_refusal,
    paused_by,
    rate_limited,
    tool_trace,
)
from auditor.graph.refine.models import RunnerKind, RunStatus
from auditor.graph.refine.prompts import RUN_ANSWER_SCHEMA, SYSTEM_PROMPT
from auditor.graph.refine.runner import RefinementJob
from auditor.graph.refine.service import RefinementService
from auditor.user_settings import CodexPrice, UserSettings

ANSWER = {"summary": "one edge", "proposed": 1, "stopped_because": "done"}
PRICES = {"gpt-5.1-codex": CodexPrice(input=1.0, output=10.0)}
OPTIONS = CodexOptions(
    model="gpt-5.1-codex",
    cwd=Path("/tmp/repo"),
    home=Path("/tmp/home"),
    auth=Path("/tmp/home/auth.json"),
    system_prompt=SYSTEM_PROMPT,
    output_schema=dict(RUN_ANSWER_SCHEMA),
    max_budget_usd=0.25,
)


def done(**over: Any) -> Turn:
    """A turn that completed and answered with the schema's JSON."""
    return Turn(final_response=json.dumps(ANSWER), **over)


def outcome(turn: Any, *, options: CodexOptions = OPTIONS, prices=PRICES, **kw):
    return from_turn(turn, options=options, prices=prices, thread_id="thread-1", **kw)


@pytest.mark.parametrize(
    ("name", "body", "named"),
    [
        ("config.toml", '[mcp_servers.other]\ncommand = "x"\n', "mcp_servers"),
        ("config.toml", "[hooks]\n", None),
        ("config.toml", 'model = "gpt-5"\n', None),
        ("hooks.json", '{"hooks": {"Stop": [{"matcher": "*"}]}}', "hooks"),
        ("hooks.json", "{}", None),
        ("hooks.json", "{not json", "cannot be read"),
    ],
    ids=[
        "toml-servers",
        "toml-empty-hooks",
        "toml-plain",
        "json-hooks",
        "json-empty",
        "json-broken",
    ],
)
def test_a_managed_file_refuses_the_run_for_what_it_declares(
    tmp_path, name, body, named
):
    """`/etc/codex/*` is read before `CODEX_HOME`; content refuses, existence does not."""
    managed = tmp_path / name
    managed.write_text(body, encoding="utf-8")
    refused = managed_refusal((managed,))
    if named is None:
        assert refused is None
        return
    assert "sits above CODEX_HOME" in refused
    assert named in refused


def test_no_managed_file_refuses_nothing(tmp_path):
    assert managed_refusal((tmp_path / "absent.toml", tmp_path / "absent.json")) is None


def test_the_runner_is_the_codex_kind_and_makes_one_turn_per_run():
    assert CodexRunner.kind is RunnerKind.CODEX
    assert CALLS_PER_RUN == 1


def test_the_options_that_cannot_vary_are_the_isolated_ones():
    """The SDK's own `approval_mode` default is `auto_review`, which fails Invariant 4 open."""
    assert (SANDBOX, APPROVAL_MODE, EFFORT) == ("read_only", "deny_all", "low")


def test_the_model_comes_from_codex_model_never_from_the_job(tmp_path):
    """`RefinementJob.model` is typed `ClaudeModel`, so no surface can put a Codex model on it."""
    user = UserSettings.model_validate(
        {"observer": {"runner": {"codex_model": "gpt-5-mini", "model": "sonnet"}}}
    )
    built = CodexOptions.of(
        RefinementJob(model="sonnet"),
        user,
        tmp_path,
        home=tmp_path / "h",
        auth=tmp_path / "auth.json",
    )
    assert built.model == "gpt-5-mini"


@pytest.mark.parametrize(
    ("servers", "named"),
    [
        ((), "no mcp servers"),
        (("graph", "user-thing"), "unexpected mcp servers"),
        (("graph",), None),
    ],
)
def test_the_session_is_refused_unless_graph_is_the_only_server(servers, named):
    refused = OPTIONS.refusal(servers)
    assert (named in refused) if named else (refused is None)


def test_usage_counts_cache_tokens_as_input_and_reasoning_as_output():
    turn = done(
        usage=Usage(
            total=Breakdown(
                input_tokens=100,
                cached_input_tokens=30,
                cache_write_input_tokens=10,
                output_tokens=20,
                reasoning_output_tokens=5,
            )
        )
    )
    usage = codex_usage(turn)
    assert (usage.input_tokens, usage.output_tokens) == (140, 25)
    assert usage.num_turns == CALLS_PER_RUN


def test_a_turn_that_reported_no_usage_is_still_marked_estimated():
    """`TurnResult.usage` stays `None` until a token-usage notification arrives."""
    usage = codex_usage(done())
    assert (usage.input_tokens, usage.output_tokens, usage.cost_usd) == (0, 0, 0.0)
    assert usage.cost_estimated is True


def test_every_codex_run_records_an_estimated_cost():
    turn = done(
        usage=Usage(total=Breakdown(input_tokens=100_000, output_tokens=10_000))
    )
    ended = outcome(turn)
    assert ended.status is RunStatus.SUCCEEDED
    assert ended.usage.cost_usd == pytest.approx(0.2)
    assert ended.usage.cost_estimated is True


def test_an_unpriced_model_records_zero_dollars_rather_than_a_wrong_one():
    """Neither the shipped table nor the user's overrides name it, so the day counts runs."""
    nameless = OPTIONS.model_copy(update={"model": "o9-imaginary"})
    ended = outcome(
        done(usage=Usage(total=Breakdown(input_tokens=9_000_000))),
        options=nameless,
        prices={},
    )
    assert ended.usage.cost_usd == 0.0
    assert ended.usage.cost_estimated is True


def test_a_completed_turn_with_no_schema_answer_is_a_failure():
    assert (
        outcome(Turn(final_response="I had a look around")).status is RunStatus.FAILED
    )
    assert "no structured answer" in (outcome(Turn(final_response="hi")).error or "")


def test_a_run_over_the_per_run_ceiling_is_aborted_after_the_turn():
    """`TurnStartParams` carries no budget field, so this is the only place it can be checked."""
    turn = done(
        usage=Usage(total=Breakdown(input_tokens=1_000_000, output_tokens=100_000))
    )
    ended = from_turn(turn, options=OPTIONS, prices=PRICES, thread_id=None)
    assert ended.status is RunStatus.ABORTED
    assert "over_budget" in (ended.error or "")
    assert f"{OPTIONS.max_budget_usd:.4f}" in (ended.error or "")


@pytest.mark.parametrize(
    ("message", "status", "word"),
    [
        ("Unauthorized: token expired", RunStatus.FAILED, "paused:auth"),
        ("429 rate limit exceeded", RunStatus.ABORTED, "paused:ratelimit"),
        ("insufficient quota", RunStatus.ABORTED, "paused:billing"),
    ],
)
def test_a_failed_turn_maps_to_the_pause_words_the_loop_reads(message, status, word):
    assert paused_by(message) == (status, word)
    ended = outcome(Turn(status="failed", error=TurnError(message=message)))
    assert ended.status is status
    assert ended.error == word


def test_a_failed_turn_with_no_pause_word_is_a_plain_failure():
    ended = outcome(Turn(status="failed", error=TurnError(message="disk on fire")))
    assert ended.status is RunStatus.FAILED
    assert "disk on fire" in (ended.error or "")


def test_an_interrupted_turn_is_aborted_not_failed():
    assert outcome(Turn(status="interrupted")).status is RunStatus.ABORTED


def test_the_trace_unwraps_root_models_and_keeps_both_item_kinds():
    items = (
        Rooted(
            root=McpCall(tool="propose", arguments={"kind": "add_edge"}, duration_ms=12)
        ),
        Rooted(root=Command(command="rg needle", duration_ms=3)),
        Rooted(root=Turn()),
    )
    trace = tool_trace(items)
    assert [call.tool for call in trace] == ["mcp__graph__propose", "Bash"]
    assert "12 ms" in trace[0].detail


@pytest.mark.parametrize(
    ("used", "ceiling", "paused"),
    [(49, 0.5, False), (50, 0.5, True), (100, 0.5, True), (10, 1.0, False)],
)
def test_the_rate_limit_compares_a_percent_with_a_fraction_on_one_scale(
    used, ceiling, paused
):
    answer = rate_limited(
        RateLimit(used_percent=used, resets_at=1234.0), max_utilization=ceiling
    )
    assert (answer is not None) is paused
    if paused:
        assert answer == "paused:ratelimit until 1234.0"


def test_an_account_that_reported_no_window_pauses_nothing():
    """`RateLimitSnapshot.primary` is optional, so this arm is reachable in production."""
    assert rate_limited(None, max_utilization=0.5) is None


def _runner(service: RefinementService, factory) -> CodexRunner:
    """A runner over a scripted factory, with a private home and auth inside the checkout.

    `managed_settings` names a file that does not exist, so a machine with an organisation
    managed Codex install cannot change what these seven tests measure.
    """
    auth = service.root / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    return CodexRunner(
        service,
        factory,
        home=service.root / "codex-home",
        auth=auth,
        managed_settings=(service.root / "no-such-managed-config.toml",),
    )


async def test_a_run_records_the_codex_kind_an_estimate_and_the_brief_it_was_given(
    refine_service: RefinementService,
):
    seen: list[Any] = []
    spent = Usage(total=Breakdown(input_tokens=1000, output_tokens=100))
    sessions: list[Any] = []
    runner = _runner(
        refine_service, codex_factory(done(usage=spent), seen=seen, sessions=sessions)
    )
    product = await runner.run(RefinementJob(scope="impl.py"))
    stored = await refine_service.index.runs.run(product.run.run_id)
    assert stored.runner is RunnerKind.CODEX
    assert stored.status is RunStatus.SUCCEEDED
    assert stored.usage.cost_estimated is True
    assert sessions[0].thread.prompt == product.brief.render()
    assert seen[0].model == ""


async def test_a_codex_run_is_recorded_under_the_codex_model_never_a_claude_tier(
    refine_service: RefinementService,
):
    """D5: `--runner codex --model sonnet` stamped `sonnet` on the row, and the gate read it."""
    runner = _runner(refine_service, codex_factory(done()))
    product = await runner.run(RefinementJob(scope="impl.py", model="sonnet"))
    stored = await refine_service.index.runs.run(product.run.run_id)
    assert stored.model == refine_service.user.observer.runner.codex_model
    assert stored.model != "sonnet"


async def test_a_proposal_the_turn_made_really_reaches_the_run(
    refine_service: RefinementService,
):
    """The fake calls the bound tools, so a renamed tool or a mis-wired handler fails here too."""
    proposal = {
        "kind": "add_edge",
        "src": "impl.py::Impl.run",
        "dst": "svc.py::load_user",
        "edge_kind": "calls",
        "name": "load_user",
        "reason": "Impl.run calls load_user, which svc.py defines",
    }
    turn = done(items=(Rooted(root=McpCall(tool="propose", arguments=proposal)),))
    runner = _runner(refine_service, codex_factory(turn))
    product = await runner.run(RefinementJob(scope="impl.py"))
    report = await refine_service.status(product.run.run_id)
    # the verdict, whichever way it went: this test is about the wiring, not the verifier
    assert report.staged or report.rejected
    stored = await refine_service.index.runs.run(product.run.run_id)
    assert [call.tool for call in stored.tool_trace] == ["mcp__graph__propose"]


async def test_a_run_whose_session_loaded_a_foreign_server_is_aborted(
    refine_service: RefinementService,
):
    runner = _runner(refine_service, codex_factory(done(), servers=("graph", "notion")))
    product = await runner.run(RefinementJob(scope="impl.py"))
    stored = await refine_service.index.runs.run(product.run.run_id)
    assert stored.status is RunStatus.ABORTED
    assert "unexpected mcp servers" in (stored.error or "")


async def test_a_rate_limited_account_stops_the_run_before_the_turn(
    refine_service: RefinementService,
):
    sessions: list[Any] = []
    runner = _runner(
        refine_service,
        codex_factory(
            done(), limit=RateLimit(used_percent=99, resets_at=42.0), sessions=sessions
        ),
    )
    product = await runner.run(RefinementJob(scope="impl.py"))
    stored = await refine_service.index.runs.run(product.run.run_id)
    assert stored.status is RunStatus.ABORTED
    assert stored.error == "paused:ratelimit until 42.0"
    assert sessions[0].thread is None


async def test_a_client_that_raises_closes_the_row_rather_than_orphaning_it(
    refine_service: RefinementService,
):
    runner = _runner(
        refine_service, codex_factory(RuntimeError("turn completed event not received"))
    )
    product = await runner.run(RefinementJob(scope="impl.py"))
    stored = await refine_service.index.runs.run(product.run.run_id)
    assert stored.status is RunStatus.FAILED
    assert "turn completed event not received" in (stored.error or "")


async def test_a_runner_built_with_no_factory_fails_the_run_it_opened(
    refine_service: RefinementService,
):
    """Invariant 2: even the refusal that never reached a turn is a `graph_runs` row."""
    runner = _runner(refine_service, None)
    product = await runner.run(RefinementJob(scope="impl.py"))
    stored = await refine_service.index.runs.run(product.run.run_id)
    assert stored.status is RunStatus.FAILED
    assert "no client factory" in (stored.error or "")
