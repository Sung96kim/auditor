"""What a runner does with a run: opens it, records the brief, proposes, and closes it once."""

import pytest
from graph._support import with_lock_timeout

from auditor.graph.model import EdgeKind
from auditor.graph.refine.lock import rebuild_lock
from auditor.graph.refine.models import (
    ClientKind,
    ProducerKind,
    Proposal,
    RefinementKind,
    RefinementStatus,
    RefinementTarget,
    RunnerKind,
    RunStatus,
    TriggerKind,
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
    outcome = await runner.run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert outcome.status is RunStatus.SUCCEEDED
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
    outcome = await FakeRunner(refine_service).run(RefinementJob())
    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.summary == "0 proposed"
    assert outcome.usage.num_turns == 1


async def test_the_models_own_summary_is_what_the_outcome_carries(
    refine_service: RefinementService,
):
    answer = RunAnswer(summary="one edge", proposed=1, stopped_because="done")
    outcome = await FakeRunner(refine_service, script=[GOOD], answer=answer).run(
        RefinementJob()
    )
    assert outcome.summary == "one edge"


async def test_a_runner_that_gives_up_aborts_the_run_and_stores_nothing(
    refine_service: RefinementService,
):
    """Invariant 2: the row exists with its cost, but `abort` promises nothing, so it keeps
    nothing."""
    outcome = await FakeRunner(refine_service, script=[GOOD], fail_with="boom").run(
        RefinementJob()
    )
    (row,) = await refine_service.index.runs.runs()
    assert (outcome.status, outcome.error) == (RunStatus.ABORTED, "boom")
    assert (row.status, row.error) == (RunStatus.ABORTED, "boom")
    assert row.usage.num_turns == 2
    assert await refine_service.index.refinements.of_run(row.run_id) == []


async def test_a_refused_proposal_is_stored_and_the_run_still_succeeds(
    refine_service: RefinementService,
):
    """Spec 9.2 stores every rejection, and one bad proposal is not a failed run."""
    outcome = await FakeRunner(refine_service, script=[INVALID]).run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert outcome.status is RunStatus.SUCCEEDED
    stored = await refine_service.index.refinements.of_run(row.run_id)
    assert [r.status for r in stored] == [RefinementStatus.REJECTED]
    assert [call.detail for call in row.tool_trace] == ["rejected"]


async def test_a_commit_the_service_refuses_is_not_followed_by_an_abort(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """`commit` closes the run before it can refuse, so an `abort` after it would raise "not open
    in this process" out of the runner's own error handler."""
    with_lock_timeout(refine_service, 0.05)
    aborted: list[str] = []

    async def spy(run_id, reason, **kwargs):
        aborted.append(run_id)
        raise AssertionError("the runner aborted a run the commit had already finished")

    monkeypatch.setattr(refine_service, "abort", spy)
    async with rebuild_lock(refine_service.identity):
        outcome = await FakeRunner(refine_service, script=[GOOD]).run(RefinementJob())
    (row,) = await refine_service.index.runs.runs()
    assert outcome.status is RunStatus.FAILED
    assert "rebuild lock" in (outcome.error or "")
    assert row.status is RunStatus.FAILED
    assert aborted == []


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
