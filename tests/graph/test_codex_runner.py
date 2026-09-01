"""The Codex runner without the SDK: the option set, the turn mapping and the ceilings."""

import asyncio
import json
import tomllib
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
    TurnStatus,
    Usage,
    codex_factory,
)

from auditor.graph.refine.client import ServerStatus
from auditor.graph.refine.codex_home import CodexHome
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


def _loaded(*names: str, handshake: str = "shim-1") -> tuple[ServerStatus, ...]:
    """`mcpServerStatus/list` as this run would read it, `graph` answering ``handshake``."""
    return tuple(
        ServerStatus(name=name, handshake=handshake if name == "graph" else None)
        for name in names
    )


@pytest.mark.parametrize(
    ("servers", "named"),
    [
        ((), "no mcp servers"),
        (_loaded("graph", "user-thing"), "unexpected mcp servers"),
        (_loaded("graph"), None),
    ],
    ids=["none", "foreign", "ours"],
)
def test_the_session_is_refused_unless_graph_is_the_only_server(servers, named):
    refused = OPTIONS.refusal(servers, handshake="shim-1")
    assert (named in refused) if named else (refused is None)


def test_a_graph_server_that_is_another_run_s_shim_is_refused():
    """Two concurrent runs each write a `config.toml`; names alone cannot tell them apart."""
    servers = (ServerStatus(name="graph", handshake="shim-2"),)
    assert "not this run's shim" in (OPTIONS.refusal(servers, handshake="shim-1") or "")


def test_a_graph_server_that_has_not_answered_yet_is_not_refused():
    """`McpServerStatus.serverInfo` is optional, so failing closed here would abort every run."""
    servers = (ServerStatus(name="graph", handshake=None),)
    assert OPTIONS.refusal(servers, handshake="shim-1") is None


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
        ("unauthorized_client", RunStatus.FAILED, "paused:auth"),
        ("error 401 returned", RunStatus.FAILED, "paused:auth"),
        ("429 rate limit exceeded", RunStatus.ABORTED, "paused:ratelimit"),
        ("You have been rate limited", RunStatus.ABORTED, "paused:ratelimit"),
        ("insufficient quota", RunStatus.ABORTED, "paused:billing"),
        ("insufficient_quota", RunStatus.ABORTED, "paused:billing"),
        ("billing_hard_limit_reached", RunStatus.ABORTED, "paused:billing"),
    ],
)
def test_a_failed_turn_maps_to_the_pause_words_the_loop_reads(message, status, word):
    assert paused_by(message) == (status, word)
    ended = outcome(Turn(status=TurnStatus.failed, error=TurnError(message=message)))
    assert ended.status is status
    assert ended.error == word


@pytest.mark.parametrize(
    "message",
    ["read 4013 tokens", "wrote 429847 bytes", "4013", "42900"],
    ids=["401-inside-a-count", "429-inside-a-count", "bare-4013", "bare-42900"],
)
def test_a_code_buried_in_a_longer_number_does_not_pause_the_repo(message):
    """L6: only the numeric codes are anchored, and against digits, not word characters.

    A word boundary would have taken `insufficient_quota` and `unauthorized_client` with it,
    which is the shape these arrive in, so the table above pins both halves.
    """
    assert paused_by(message) is None


def test_a_failed_turn_with_no_pause_word_is_a_plain_failure():
    ended = outcome(
        Turn(status=TurnStatus.failed, error=TurnError(message="disk on fire"))
    )
    assert ended.status is RunStatus.FAILED
    assert "disk on fire" in (ended.error or "")


@pytest.mark.parametrize(
    "status", [TurnStatus.interrupted, "interrupted"], ids=["enum", "wire-string"]
)
def test_an_interrupted_turn_is_aborted_not_failed(status):
    """C1's other half: `str(TurnStatus.interrupted)` missed `STOPPED_STATUSES` too."""
    assert outcome(Turn(status=status)).status is RunStatus.ABORTED


@pytest.mark.parametrize(
    "status", [TurnStatus.completed, "completed"], ids=["enum", "wire-string"]
)
def test_a_completed_turn_is_read_off_the_enum_not_off_its_repr(status):
    """C1: `str()` before `.value` made every real Codex turn `failed`, in both venvs."""
    ended = outcome(done(status=status))
    assert ended.status is RunStatus.SUCCEEDED
    assert ended.summary == "one edge"


def _items() -> tuple[Any, ...]:
    """Two calls the runner keeps and one item kind it drops, in the order they happened."""
    return (
        Rooted(
            root=McpCall(tool="propose", arguments={"kind": "add_edge"}, duration_ms=12)
        ),
        Rooted(root=Command(command="rg needle", duration_ms=3000)),
        Rooted(root=Turn()),
    )


def test_the_trace_unwraps_root_models_and_keeps_both_item_kinds():
    trace = tool_trace(_items())
    assert [call.tool for call in trace] == ["mcp__graph__propose", "Bash"]
    assert "12 ms" in trace[0].detail


def test_each_call_is_stamped_where_it_ran_not_where_the_turn_was_mapped():
    """One shared timestamp per run lost the ordering signal the trace exists to carry."""
    trace = tool_trace(_items(), started_at=1000.0)
    assert [call.ts for call in trace] == [1000.0, 1000.012]


def test_a_turn_that_reported_no_start_falls_back_to_one_stamp():
    """`Turn.startedAt` is optional, so the old behaviour is still the documented fallback."""
    trace = tool_trace(_items())
    assert len({call.ts for call in trace}) == 1


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


def _writing_factory(homes: list[Path], written: list[dict[str, str]]):
    """A factory that really writes the private home the way `CodexClient.__aenter__` does.

    ``homes`` collects the leaf each run was given and ``written`` the `[mcp_servers.graph]`
    table its own `config.toml` ended up holding.
    """

    def factory(options: CodexOptions, tools: Any) -> Any:
        homes.append(options.home)
        CodexHome(
            home=options.home,
            root=options.cwd,
            server_url=f"http://127.0.0.1:{len(homes)}/mcp",
            model=options.model,
        ).write(auth=options.auth)
        config = (options.home / "config.toml").read_text(encoding="utf-8")
        written.append(tomllib.loads(config)["mcp_servers"]["graph"])
        return codex_factory(done())(options, tools)

    return factory


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


async def test_two_interleaved_runs_never_share_one_private_home(
    refine_service: RefinementService,
):
    """H2: one home meant run B's `config.toml` sent run A's binary at run B's shim."""
    homes: list[Path] = []
    written: list[dict[str, str]] = []
    runner = _runner(refine_service, _writing_factory(homes, written))
    await asyncio.gather(
        runner.run(RefinementJob(scope="impl.py")),
        runner.run(RefinementJob(scope="svc.py")),
    )
    assert len(set(homes)) == 2
    assert len({entry["url"] for entry in written}) == 2


async def test_a_run_s_private_home_is_written_and_then_removed_with_the_run(
    refine_service: RefinementService,
):
    """A home per run is only isolation if the run really writes one and takes it away again."""
    homes: list[Path] = []
    runner = _runner(refine_service, _writing_factory(homes, []))
    await runner.run(RefinementJob(scope="impl.py"))
    assert homes[0].parent == refine_service.root / "codex-home"
    assert not homes[0].exists()
    # the home holds a symlink to the user's real credentials, which the sweep must not follow
    assert (refine_service.root / "auth.json").is_file()


async def test_a_session_that_loaded_another_run_s_shim_is_aborted(
    refine_service: RefinementService,
):
    """The crossed-`config.toml` case: the name is right and the server is somebody else's."""
    runner = _runner(refine_service, codex_factory(done(), answered="another-run"))
    product = await runner.run(RefinementJob(scope="impl.py"))
    stored = await refine_service.index.runs.run(product.run.run_id)
    assert stored.status is RunStatus.ABORTED
    assert "not this run's shim" in (stored.error or "")


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
