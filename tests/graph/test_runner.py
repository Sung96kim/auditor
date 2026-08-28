"""What a runner does with a run: opens it, records the brief, proposes, and closes it once."""

from collections.abc import Mapping
from typing import Any

import pytest
from graph._support import with_lock_timeout

from auditor.graph.model import EdgeKind
from auditor.graph.refine.brief import Brief, BriefLimits
from auditor.graph.refine.lock import rebuild_lock
from auditor.graph.refine.models import (
    ClientKind,
    ProducerKind,
    Proposal,
    ProposalOutcome,
    RefinementKind,
    RefinementStatus,
    RefinementTarget,
    RunnerKind,
    RunOutcome,
    RunStatus,
    TriggerKind,
    Verdict,
)
from auditor.graph.refine.prompts import SYSTEM_PROMPT_SHA, RunAnswer
from auditor.graph.refine.runner import PROPOSE_TOOL, FakeRunner, RefinementJob
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
#: a payload `Proposal`'s own validators refuse, so the service stores the rejection
INVALID = {"kind": "add_edge", "target": {}, "reason": ""}


async def test_a_scripted_run_lands_its_proposal_and_records_how(
    refine_service: RefinementService,
):
    runner = FakeRunner(refine_service, script=[GOOD])
    await runner.run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert (row.status, row.runner) == (RunStatus.SUCCEEDED, RunnerKind.FAKE)
    assert row.system_prompt_sha == SYSTEM_PROMPT_SHA
    assert row.prompt and "Refinement brief" in row.prompt
    assert [call.tool for call in row.tool_trace] == [PROPOSE_TOOL]
    assert row.usage.num_turns == 2
    stored = await refine_service.index.refinements.of_run(row.run_id)
    assert [r.status for r in stored] == [RefinementStatus.PENDING]


async def test_the_brief_the_run_records_is_the_brief_it_was_given(
    refine_service: RefinementService,
):
    """Invariant 2 wants the verbatim prompt, so a later reader can see what was asked."""
    runner = FakeRunner(refine_service, script=[GOOD])
    await runner.run(RefinementJob(scope="impl.py"))
    (row,) = await refine_service.index.runs.runs()
    assert "scope: impl.py" in (row.prompt or "")


async def test_an_empty_script_commits_without_locking_or_rebuilding(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """A run that proposed nothing has nothing to retire, and a real rebuild costs seconds."""

    def refuse(*args, **kwargs):
        raise AssertionError("an empty run took the rebuild lock")

    monkeypatch.setattr("auditor.graph.refine.service.rebuild_lock", refuse)
    await FakeRunner(refine_service).run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert row.status is RunStatus.SUCCEEDED
    assert row.summary == "nothing staged"
    assert row.usage.num_turns == 1


async def test_the_producers_own_summary_is_what_the_row_records(
    refine_service: RefinementService,
):
    """The system prompt asks for that line, so it has to reach the human who reads the run."""
    answer = RunAnswer(summary="one edge", proposed=1, stopped_because="done")
    await FakeRunner(refine_service, script=[GOOD], answer=answer).run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert row.summary == "one edge"


async def test_a_producer_with_nothing_to_say_leaves_the_counted_line(
    refine_service: RefinementService,
):
    await FakeRunner(refine_service, script=[GOOD]).run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert row.summary == "1 committed, 0 rejected"


async def test_a_runner_that_gives_up_aborts_the_run_and_stores_nothing(
    refine_service: RefinementService,
):
    """Invariant 2: the row exists with its cost, but `abort` promises nothing, so it keeps
    nothing."""
    await FakeRunner(refine_service, script=[GOOD], stop="boom").run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert (row.status, row.error) == (RunStatus.ABORTED, "boom")
    assert row.usage.num_turns == 2
    assert await refine_service.index.refinements.of_run(row.run_id) == []


async def test_a_refused_proposal_is_stored_and_the_run_still_succeeds(
    refine_service: RefinementService,
):
    """Spec 9.2 stores every rejection, and one bad proposal is not a failed run."""
    await FakeRunner(refine_service, script=[INVALID]).run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert row.status is RunStatus.SUCCEEDED
    stored = await refine_service.index.refinements.of_run(row.run_id)
    assert [r.status for r in stored] == [RefinementStatus.REJECTED]
    assert [call.detail for call in row.tool_trace] == ["rejected"]


async def test_a_commit_the_service_refuses_is_not_followed_by_an_abort(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """`commit` closes the run before it can refuse, so an `abort` after it would raise "not open
    in this process" out of the runner's own error handler."""
    with_lock_timeout(refine_service, 0.05)
    terminated: list[str] = []

    async def spy(run_id, *args, **kwargs):
        terminated.append(run_id)
        raise AssertionError("the runner closed a run the commit had already finished")

    monkeypatch.setattr(refine_service, "terminate", spy)
    async with rebuild_lock(refine_service.identity):
        await FakeRunner(refine_service, script=[GOOD]).run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert row.status is RunStatus.FAILED
    assert "rebuild lock" in (row.error or "")
    assert terminated == []


def test_a_job_defaults_to_a_manual_cli_run_over_the_whole_repo():
    job = RefinementJob()
    assert (job.scope, job.model, job.session_id) == ("", None, None)
    assert (job.producer, job.client, job.trigger) == (
        ProducerKind.CLI,
        ClientKind.CLI,
        TriggerKind.MANUAL,
    )


async def test_a_job_names_who_asked_on_the_run_row(refine_service: RefinementService):
    await FakeRunner(refine_service).run(
        RefinementJob(
            producer=ProducerKind.AGENT,
            client=ClientKind.CLAUDE_CODE,
            trigger=TriggerKind.EDIT,
            session_id="s-1",
            agent_name="refiner",
            model="sonnet",
        )
    )
    (row,) = await refine_service.index.runs.runs()
    assert (row.producer, row.client, row.trigger_kind) == (
        ProducerKind.AGENT,
        ClientKind.CLAUDE_CODE,
        TriggerKind.EDIT,
    )
    assert (row.session_id, row.agent_name, row.model) == ("s-1", "refiner", "sonnet")


async def test_a_job_without_a_model_takes_the_configured_one(
    refine_service: RefinementService,
):
    await FakeRunner(refine_service).run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert row.model == refine_service.user.observer.runner.model


async def test_the_product_carries_what_the_commit_landed(
    refine_service: RefinementService,
):
    """Both surfaces report the verdicts, so the commit result travels with the outcome rather
    than being re-derived from the stored rows, which keep no verify status."""
    product = await FakeRunner(refine_service, script=[GOOD]).run(RefinementJob())
    assert product.landed is not None
    assert product.landed.landed == 1
    assert [v.kind for v in product.landed.committed] == [RefinementKind.ADD_EDGE]


async def test_a_run_that_did_not_commit_landed_nothing(
    refine_service: RefinementService,
):
    product = await FakeRunner(refine_service, script=[GOOD], stop="boom").run(
        RefinementJob()
    )
    assert product.landed is None


async def test_an_unshapeable_proposal_is_traced_and_the_run_still_closes(
    refine_service: RefinementService,
):
    """`_judge` refuses a payload no `Proposal` can be read out of, and an unguarded producer
    would leave its own run open on the way out."""
    await FakeRunner(refine_service, script=[{"kind": "not_a_kind"}]).run(
        RefinementJob()
    )
    (row,) = await refine_service.index.runs.runs()
    assert row.status is RunStatus.SUCCEEDED
    assert row.finished_at is not None
    assert "not a proposal" in row.tool_trace[0].detail
    assert refine_service.registry.open_runs == {}


async def test_a_runner_can_stop_a_run_failed_as_well_as_aborted(
    refine_service: RefinementService,
):
    """spec 5.3 keeps `aborted` for a cap and `failed` for a producer that broke, so a producer
    has to be able to say which."""
    await FakeRunner(
        refine_service, stop="the client died", stop_status=RunStatus.FAILED
    ).run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert (row.status, row.error) == (RunStatus.FAILED, "the client died")


async def test_closing_a_run_the_registry_evicted_does_not_raise(
    refine_service: RefinementService,
):
    """An evicted run is already stamped `skipped`; raising out of `run()` would discard the
    payload of a run that really happened and name the wrong problem."""
    refine_service.registry.max_open = 1
    run = await refine_service.begin()
    outcome = RunOutcome.of(RunStatus.ABORTED, error="cancelled")
    runner = FakeRunner(refine_service)
    await refine_service.begin()  # evicts the first run
    product = await runner._close(
        run, Brief(limits=BriefLimits(max_changes=1, max_targets=1)), outcome
    )
    (evicted,) = [
        r for r in await refine_service.index.runs.runs() if r.run_id == run.run_id
    ]
    assert product.landed is None
    assert evicted.status is RunStatus.SKIPPED


async def test_a_proposer_replaces_the_service_and_stores_nothing(
    refine_service: RefinementService,
):
    """Invariant 2: an eval's proposals go to a judge, so the run commits no refinement row."""
    seen: list[tuple[str, str]] = []

    async def judge(run_id: str, raw: Mapping[str, Any]) -> Verdict:
        seen.append((run_id, str(raw.get("kind"))))
        return Verdict(outcome=ProposalOutcome.STAGED, kind=RefinementKind.ADD_EDGE)

    runner = FakeRunner(refine_service, proposer=judge, script=[GOOD])
    product = await runner.run(RefinementJob())
    assert [kind for _, kind in seen] == ["add_edge"]
    assert seen[0][0] == product.run.run_id
    assert await refine_service.index.refinements.refinements() == []
    assert product.landed is not None and product.landed.committed == ()


async def test_the_proposers_verdict_is_what_the_trace_records(
    refine_service: RefinementService,
):
    async def judge(_run_id: str, _raw: Mapping[str, Any]) -> Verdict:
        return Verdict(outcome=ProposalOutcome.REJECTED, kind=RefinementKind.ADD_EDGE)

    await FakeRunner(refine_service, proposer=judge, script=[GOOD]).run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert [call.detail for call in row.tool_trace] == [ProposalOutcome.REJECTED.value]


async def test_without_a_proposer_the_runner_uses_the_services_own(
    refine_service: RefinementService,
):
    assert FakeRunner(refine_service).proposer == refine_service.propose


async def test_an_eval_run_records_its_trigger_on_the_row(
    refine_service: RefinementService,
):
    await FakeRunner(refine_service).run(RefinementJob(trigger=TriggerKind.EVAL))
    (row,) = await refine_service.index.runs.runs()
    assert row.trigger_kind is TriggerKind.EVAL
