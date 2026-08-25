"""The refinement lifecycle (spec 9.1), driven by hand with no runner."""

import asyncio
import time

import pytest
from pydantic import ValidationError

from auditor.database.refinements import RefinementsDB
from auditor.graph.model import EdgeKind
from auditor.graph.refine.lock import rebuild_lock
from auditor.graph.refine.models import (
    Proposal,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    RunStatus,
    Tier,
)
from auditor.graph.refine.service import (
    ProposalOutcome,
    RefinementRefused,
    RefinementService,
    RefusalKind,
)
from auditor.graph.refine.verify import VerifyStatus
from auditor.user_settings import UserSettings

CALL_EDGE = Proposal(
    kind=RefinementKind.ADD_EDGE,
    target=RefinementTarget(
        src="impl.py::Impl.run",
        dst="svc.py::load_user",
        edge_kind=EdgeKind.CALLS,
        name="load_user",
    ),
    reason="Impl.run calls load_user, which svc.py defines",
    confidence=0.9,
)
#: a second proposal that stages: another kind, another target, no edge to collide with
NOTE = Proposal(
    kind=RefinementKind.ANNOTATE_NODE,
    target=RefinementTarget(node_id="impl.py::Impl.run"),
    payload=RefinementPayload(annotation="the entry point"),
    reason="worth a note",
)
#: the name `Impl.run` does not call, which is what the verifier refuses
UNCALLED = CALL_EDGE.model_copy(
    update={
        "target": CALL_EDGE.target.model_copy(update={"name": "never_called"}),
        "reason": "a name Impl.run does not call",
        "confidence": 0.0,
    }
)


def with_lock_timeout(service: RefinementService, seconds: float) -> RefinementService:
    """Shrink this service's rebuild-lock budget, so a held lock is a fast refusal."""
    service.settings = service.settings.model_copy(
        update={
            "graph": service.settings.graph.model_copy(
                update={"rebuild_lock_timeout_seconds": seconds}
            )
        }
    )
    return service


async def test_a_manual_run_is_attributable(refine_service: RefinementService):
    run = await refine_service.begin(scope="")
    assert run.runner.value == "none"
    assert run.status is RunStatus.QUEUED
    stored = await refine_service.index.runs.run(run.run_id)
    assert stored is not None and stored.repo_identity == refine_service.identity


async def test_a_verified_bare_call_stages_as_tier_b_and_pending(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    verdict = await refine_service.propose(run.run_id, CALL_EDGE)
    assert verdict.outcome is ProposalOutcome.STAGED
    assert verdict.verify is VerifyStatus.OK
    assert verdict.tier is Tier.B
    # no graph_evals row on this repo, so spec 10.3 makes tier B behave as tier C
    assert verdict.status is RefinementStatus.PENDING
    assert verdict.refinement_id == 0


async def test_a_rejection_is_stored_the_moment_it_is_made(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    verdict = await refine_service.propose(run.run_id, UNCALLED)
    assert verdict.outcome is ProposalOutcome.REJECTED
    assert verdict.verify is VerifyStatus.NOT_A_DEFINER
    stored = await refine_service.index.refinements.refinement(verdict.refinement_id)
    assert stored is not None and stored.status is RefinementStatus.REJECTED
    assert "never_called" in stored.reason or verdict.detail in stored.reason


async def test_a_payload_with_no_reason_is_stored_as_a_rejection(
    refine_service: RefinementService,
):
    """`Proposal` owns the reason rule and raises, so the service never re-checks it: it hands the
    payload to the model, catches the `ValidationError`, and stores the refusal spec 9.2 requires."""
    run = await refine_service.begin()
    verdict = await refine_service.propose(
        run.run_id,
        {
            "kind": "annotate_node",
            "target": {"node_id": "impl.py::Impl.run"},
            "payload": {"annotation": "the entry point"},
        },
    )
    assert verdict.outcome is ProposalOutcome.REJECTED
    assert verdict.refusal is RefusalKind.INVALID
    assert "needs a reason" in verdict.detail
    stored = await refine_service.index.refinements.refinement(verdict.refinement_id)
    assert stored is not None and stored.status is RefinementStatus.REJECTED


async def test_a_payload_no_lenient_read_can_rescue_raises(
    refine_service: RefinementService,
):
    """A missing `dst` is not a text rule `STORED_ROW` relaxes, so there is no proposal to
    attribute a rejection to and the caller gets the validator's error."""
    run = await refine_service.begin()
    with pytest.raises(ValidationError, match="missing"):
        await refine_service.propose(
            run.run_id, {"kind": "add_edge", "target": {"src": "impl.py::Impl.run"}}
        )


async def test_a_second_dst_for_one_name_in_one_run_collides(
    refine_service: RefinementService,
):
    """The conflict rules read committed work only, so a run naming one `(src, calls, name)` twice
    with different destinations would land both. Measured on this repo: 25 such pairs in one batch
    over the real queue."""
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    second = await refine_service.propose(
        run.run_id,
        CALL_EDGE.model_copy(
            update={
                "target": CALL_EDGE.target.model_copy(
                    update={"dst": "base.py::Base.run"}
                )
            }
        ),
    )
    assert second.outcome is ProposalOutcome.REJECTED
    assert second.refusal is RefusalKind.INTRA_BATCH
    assert len((await refine_service.status(run.run_id)).staged) == 1


async def test_an_id_this_partition_does_not_hold_is_named(
    refine_service: RefinementService,
):
    """Otherwise `rebased` prefixes it anyway and the verifier answers `no_such_path`, which
    describes a file rather than the caller's actual mistake."""
    run = await refine_service.begin()
    verdict = await refine_service.propose(
        run.run_id,
        CALL_EDGE.model_copy(
            update={
                "target": CALL_EDGE.target.model_copy(
                    update={"dst": "nowhere.py::load_user"}
                )
            }
        ),
    )
    assert verdict.outcome is ProposalOutcome.REJECTED
    assert verdict.refusal is RefusalKind.OUT_OF_PARTITION
    assert "nowhere.py::load_user" in verdict.detail


async def test_an_evicted_run_is_finished_and_its_staging_stored(
    refine_service: RefinementService,
):
    """Invariant 2: eviction drops staging that was never promised, but the row it belonged to is
    finished with a reason instead of sitting `queued` where nothing will ever reap it."""
    refine_service.registry.max_open = 1
    first = await refine_service.begin()
    await refine_service.propose(first.run_id, CALL_EDGE)
    await refine_service.begin()
    evicted = await refine_service.index.runs.run(first.run_id)
    assert evicted is not None and evicted.status is RunStatus.SKIPPED
    assert "max_open=1" in (evicted.error or "")
    stored = await refine_service.index.refinements.of_run(first.run_id)
    assert [r.status for r in stored] == [RefinementStatus.REJECTED]
    with pytest.raises(RefinementRefused, match="not open"):
        await refine_service.commit(first.run_id)


async def test_commit_inserts_stages_and_rebuilds(refine_service: RefinementService):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    result = await refine_service.commit(run.run_id)
    assert [v.outcome for v in result.committed] == [ProposalOutcome.STAGED]
    assert (result.landed, result.rebuilt) == (1, True)
    assert result.build is not None and result.build.nodes > 0
    stored = await refine_service.index.refinements.refinement(
        result.committed[0].refinement_id
    )
    assert stored is not None
    assert stored.target.src == "impl.py::Impl.run"
    assert stored.status is RefinementStatus.PENDING
    finished = await refine_service.index.runs.run(run.run_id)
    assert finished is not None and finished.status is RunStatus.SUCCEEDED


async def test_a_committed_refinement_carries_its_anchors(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    result = await refine_service.commit(run.run_id)
    rid = result.committed[0].refinement_id
    anchors = await refine_service.index.refinements.anchors([rid])
    # the verifier anchors the proposal's ids plus the queue row's `resolution_path`; `impl.py`
    # never imports `svc`, so that path is empty and the endpoints are the whole set
    assert {a.node_id for a in anchors[rid]} == {
        "impl.py::Impl.run",
        "svc.py::load_user",
    }


async def test_accepting_a_pending_refinement_makes_the_next_build_apply_it(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    result = await refine_service.commit(run.run_id)
    assert result.build is not None and result.build.refined == 0
    accepted = await refine_service.accept(result.committed[0].refinement_id)
    assert accepted.status is RefinementStatus.ACTIVE
    summary = await refine_service.rebuild()
    assert summary.refined == 1


async def test_a_second_identical_proposal_commits_as_a_confirmation(
    refine_service: RefinementService,
):
    first = await refine_service.begin()
    await refine_service.propose(first.run_id, CALL_EDGE)
    committed = await refine_service.commit(first.run_id)
    await refine_service.accept(committed.committed[0].refinement_id)
    second = await refine_service.begin()
    await refine_service.propose(second.run_id, CALL_EDGE)
    result = await refine_service.commit(second.run_id)
    stored = await refine_service.index.refinements.refinement(
        result.committed[0].refinement_id
    )
    assert stored is not None and stored.kind is RefinementKind.CONFIRM_EDGE


async def test_abort_leaves_nothing_behind_and_ends_the_run(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    aborted = await refine_service.abort(run.run_id, "the agent changed its mind")
    assert aborted.status is RunStatus.ABORTED
    assert aborted.error == "the agent changed its mind"
    assert await refine_service.index.refinements.of_run(run.run_id) == []
    with pytest.raises(RefinementRefused, match="not open"):
        await refine_service.commit(run.run_id)


async def test_the_change_cap_refuses_the_proposal_past_the_limit(
    refine_service: RefinementService,
):
    refine_service.user = UserSettings.model_validate(
        {"observer": {"limits": {"max_changes_per_run": 1}}}
    )
    run = await refine_service.begin()
    assert (
        await refine_service.propose(run.run_id, CALL_EDGE)
    ).outcome is ProposalOutcome.STAGED
    second = await refine_service.propose(run.run_id, NOTE)
    assert second.outcome is ProposalOutcome.REJECTED
    assert second.refusal is RefusalKind.OVER_CAP
    assert "max_changes_per_run" in second.detail


async def test_a_proposal_outside_the_run_scope_is_refused(
    refine_service: RefinementService,
):
    run = await refine_service.begin(scope="svc.py")
    verdict = await refine_service.propose(run.run_id, CALL_EDGE)
    assert verdict.outcome is ProposalOutcome.REJECTED
    assert verdict.refusal is RefusalKind.OUT_OF_SCOPE


async def test_status_reports_what_is_staged_here(refine_service: RefinementService):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    report = await refine_service.status(run.run_id)
    assert report.staged_here is True
    assert [v.kind for v in report.staged] == [RefinementKind.ADD_EDGE]
    assert report.committed == ()


async def test_status_of_a_run_this_process_did_not_open_says_so(
    refine_service: RefinementService, refine_service_other: RefinementService
):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    report = await refine_service_other.status(run.run_id)
    assert report.staged_here is False
    assert report.staged == ()


@pytest.mark.parametrize(
    ("method", "expected"),
    [("revert", RefinementStatus.REVERTED), ("pin", RefinementStatus.PINNED)],
)
async def test_the_hand_transitions(
    refine_service: RefinementService, method: str, expected: RefinementStatus
):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    result = await refine_service.commit(run.run_id)
    moved = await getattr(refine_service, method)(result.committed[0].refinement_id)
    assert moved.status is expected


async def test_a_terminal_refinement_refuses_every_transition(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    verdict = await refine_service.propose(run.run_id, UNCALLED)
    with pytest.raises(RefinementRefused, match="rejected"):
        await refine_service.accept(verdict.refinement_id)


async def test_an_unknown_refinement_id_is_named_not_ignored(
    refine_service: RefinementService,
):
    with pytest.raises(RefinementRefused, match="4242"):
        await refine_service.accept(4242)


async def test_a_commit_with_nothing_staged_neither_locks_nor_rebuilds(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """Spec 6 wants a run's queue rows retired in the same lock as its insert. With no insert there
    is nothing to retire, and a rebuild of a real repo costs about 11.5 s."""

    def refuse(*args, **kwargs):
        raise AssertionError("commit took the rebuild lock for an empty run")

    run = await refine_service.begin()
    monkeypatch.setattr("auditor.graph.refine.service.rebuild_lock", refuse)
    result = await refine_service.commit(run.run_id)
    assert (result.landed, result.rebuilt, result.build) == (0, False, None)
    finished = await refine_service.index.runs.run(run.run_id)
    assert finished is not None and finished.status is RunStatus.SUCCEEDED


async def test_a_second_commit_on_one_run_is_refused_by_name(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    await refine_service.commit(run.run_id)
    with pytest.raises(RefinementRefused, match="not open"):
        await refine_service.commit(run.run_id)


async def test_the_same_proposal_twice_in_one_run_stages_once(
    refine_service: RefinementService,
):
    run = await refine_service.begin()
    first = await refine_service.propose(run.run_id, CALL_EDGE)
    assert first.outcome is ProposalOutcome.STAGED
    second = await refine_service.propose(run.run_id, CALL_EDGE)
    assert second.outcome is ProposalOutcome.REJECTED
    assert second.refusal is RefusalKind.ALREADY_STAGED
    report = await refine_service.status(run.run_id)
    assert len(report.staged) == 1


async def _boom(*args, **kwargs):
    raise RuntimeError("the build blew up")


async def test_a_build_that_blows_up_takes_back_what_the_commit_inserted(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """The rebuild runs after the insert transaction has committed, so the compensating step is
    the only thing stopping `accept` from activating a row whose commit never finished."""
    monkeypatch.setattr("auditor.graph.refine.service.GraphBuilder.rebuild", _boom)
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    with pytest.raises(RefinementRefused, match="blew up") as refusal:
        await refine_service.commit(run.run_id)
    assert run.run_id in str(refusal.value)
    finished = await refine_service.index.runs.run(run.run_id)
    assert finished is not None and finished.status is RunStatus.FAILED
    assert "blew up" in (finished.error or "")
    stored = await refine_service.index.refinements.of_run(run.run_id)
    assert [r.status for r in stored] == [RefinementStatus.REJECTED]
    with pytest.raises(RefinementRefused, match="not open"):
        await refine_service.commit(run.run_id)


async def test_a_retry_after_a_failed_commit_leaves_one_live_refinement(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """The conflict rules read active rows only, so two `pending` rows for one edge would both be
    acceptable and produce it twice. The failed run's row is rejected, so the retry is the only
    live one."""
    monkeypatch.setattr("auditor.graph.refine.service.GraphBuilder.rebuild", _boom)
    first = await refine_service.begin()
    await refine_service.propose(first.run_id, CALL_EDGE)
    with pytest.raises(RefinementRefused):
        await refine_service.commit(first.run_id)
    monkeypatch.undo()
    second = await refine_service.begin()
    await refine_service.propose(second.run_id, CALL_EDGE)
    assert (await refine_service.commit(second.run_id)).landed == 1
    rows = await refine_service.index.refinements.refinements()
    live = [r for r in rows if r.status is not RefinementStatus.REJECTED]
    assert [(r.run_id, r.target.dst) for r in live] == [
        (second.run_id, "svc.py::load_user")
    ]


async def test_an_insert_that_fails_rolls_the_whole_batch_back(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """One transaction for the batch: the second row failing has to take the first with it, or a
    commit that raised would still have half-landed."""
    written = 0
    real = RefinementsDB.write_refinement

    def explode(self, conn, refinement, anchors=()):
        nonlocal written
        written += 1
        if written == 2:
            raise RuntimeError("the insert blew up")
        return real(self, conn, refinement, anchors)

    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    await refine_service.propose(run.run_id, NOTE)
    monkeypatch.setattr(RefinementsDB, "write_refinement", explode)
    with pytest.raises(RefinementRefused, match="insert blew up"):
        await refine_service.commit(run.run_id)
    monkeypatch.undo()
    assert await refine_service.index.refinements.of_run(run.run_id) == []
    finished = await refine_service.index.runs.run(run.run_id)
    assert finished is not None and finished.status is RunStatus.FAILED


async def test_a_held_rebuild_lock_bounds_the_service_rebuild_too(
    refine_service: RefinementService,
):
    """`commit` was bounded and `rebuild` was not, and `rebuild` is what a caller runs after an
    `accept`: a wedged `auditr graph build` would hang it for ever."""
    with_lock_timeout(refine_service, 0.05)
    started = time.monotonic()
    async with rebuild_lock(refine_service.identity):
        with pytest.raises(RefinementRefused, match="rebuild lock"):
            await asyncio.wait_for(refine_service.rebuild(), timeout=10)
    assert time.monotonic() - started < 5


async def test_a_held_rebuild_lock_becomes_a_refusal_naming_the_lock(
    refine_service: RefinementService,
):
    """`flock` is per open file description, so a second handle in this process waits exactly the
    way another process would."""
    with_lock_timeout(refine_service, 0.05)
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    async with rebuild_lock(refine_service.identity):
        with pytest.raises(RefinementRefused, match="rebuild lock"):
            await refine_service.commit(run.run_id)
    finished = await refine_service.index.runs.run(run.run_id)
    assert finished is not None and finished.status is RunStatus.FAILED


async def test_a_commit_that_resolves_another_partition_is_refused(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """Every staged id was rebased with the prefix `begin` resolved. A caller that begins on the
    repo root and commits on a nested root carrying its own marker resolves a different one, and
    the conflict query would look up ids that cannot exist. The patch stands in for that root."""
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    monkeypatch.setattr(
        type(refine_service), "prefix", property(lambda self: "nested/")
    )
    with pytest.raises(RefinementRefused, match="partition"):
        await refine_service.commit(run.run_id)
    # the staging is still there: only this call's root was wrong, so the retry is the whole point
    monkeypatch.undo()
    assert (await refine_service.commit(run.run_id)).landed == 1


async def test_a_checkout_between_begin_and_commit_refuses_the_commit(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "auditor.graph.refine.service.git_output", lambda root, *args: "before"
    )
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    monkeypatch.setattr(
        "auditor.graph.refine.service.git_output", lambda root, *args: "after"
    )
    with pytest.raises(RefinementRefused, match="moved"):
        await refine_service.commit(run.run_id)
    finished = await refine_service.index.runs.run(run.run_id)
    assert finished is not None and finished.status is RunStatus.REJECTED
