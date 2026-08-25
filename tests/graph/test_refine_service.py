"""The refinement lifecycle (spec 9.1), driven by hand with no runner."""

import asyncio
import time
from contextlib import suppress
from pathlib import Path

import pytest

from auditor.database import IndexStore
from auditor.database.refinements import RefinementsDB
from auditor.graph.model import EdgeKind
from auditor.graph.refine.lock import rebuild_lock
from auditor.graph.refine.models import (
    Proposal,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunOutcome,
    RunStatus,
    Tier,
)
from auditor.graph.refine.service import (
    ProposalFacts,
    ProposalOutcome,
    RefinementRefused,
    RefinementService,
    RefusalKind,
    RunRegistry,
)
from auditor.graph.refine.verify import VerifyStatus
from auditor.models import Partition
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


async def test_a_target_no_kind_could_fill_is_stored_not_raised(
    refine_service: RefinementService,
):
    """Spec 9.2 stores every rejection, and a row that carries a complaint needs no target a build
    could apply: the read drops what it cannot use and the rest of the payload still reaches the
    reader."""
    run = await refine_service.begin()
    verdict = await refine_service.propose(
        run.run_id,
        {
            "kind": "add_edge",
            "target": {"src": "impl.py::Impl.run"},
            "reason": "a destination I forgot to name",
        },
    )
    assert verdict.refusal is RefusalKind.INVALID
    assert "target is missing" in verdict.detail
    stored = await refine_service.index.refinements.refinement(verdict.refinement_id)
    assert stored is not None and stored.status is RefinementStatus.REJECTED
    assert (stored.target.src, stored.target.dst) == ("impl.py::Impl.run", None)


async def test_a_payload_with_no_readable_kind_is_refused_not_raised(
    refine_service: RefinementService,
):
    """The kind chooses the shape, so a payload without a readable one has no row to be stored as.
    The refusal is still the service's own error type: a caller that has to tell a pydantic
    traceback from a verdict has two contracts instead of one."""
    run = await refine_service.begin()
    with pytest.raises(RefinementRefused, match="not a proposal"):
        await refine_service.propose(
            run.run_id, {"kind": "delete_edge", "target": {"src": "impl.py::Impl.run"}}
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
    # "not open in this process" is also true of a run this process never opened; an agent told
    # that looks for a bug in its own session handling instead of starting a new run
    with pytest.raises(RefinementRefused, match="was evicted"):
        await refine_service.commit(first.run_id)
    with pytest.raises(RefinementRefused, match="not open in this process"):
        await refine_service.commit("a-run-nobody-opened")


def _open_runs(most: int) -> UserSettings:
    """User settings whose only non-default is the registry cap one identity runs under."""
    return UserSettings.model_validate(
        {"observer": {"limits": {"max_open_runs": most}}}
    )


async def test_an_eviction_in_one_repo_leaves_another_repos_run_open(
    refine_service: RefinementService,
    process_runs: dict[str, RunRegistry],
    tmp_path: Path,
):
    """One MCP server holds runs from every checkout its clients named, so the registry is keyed
    by identity: on one shared registry the second repo's cap chose which of the first repo's runs
    to drop, and wrote the drop through the wrong identity, leaving a row `queued` for ever."""
    parts = (refine_service.index, refine_service.root, refine_service.settings)
    here = RefinementService(*parts, refine_service.user)
    mine = await here.begin()
    await here.propose(mine.run_id, CALL_EDGE)

    elsewhere = await IndexStore.connect(
        tmp_path / "other.db",
        repo="other",
        partition=Partition(identity=str(tmp_path / "other" / ".git")),
    )
    try:
        there = RefinementService(
            elsewhere, tmp_path, refine_service.settings, _open_runs(1)
        )
        first = await there.begin()
        await there.begin()
        dropped = await elsewhere.runs.run(first.run_id)
        assert dropped is not None and dropped.status is RunStatus.SKIPPED
        assert set(process_runs) == {here.identity, there.identity}
    finally:
        await elsewhere.aclose()

    kept = await refine_service.index.runs.run(mine.run_id)
    assert kept is not None and kept.status is RunStatus.QUEUED
    assert mine.run_id in RunRegistry.process(here.identity).open_runs
    assert await refine_service.index.refinements.of_run(mine.run_id) == []


async def test_pruning_reaps_an_evicted_run_and_its_rejections(
    refine_service: RefinementService,
):
    """`skipped` was chosen over `failed` because `prune_skipped_runs` can reap it, and the case
    eviction actually creates is a skipped run that owns rows."""
    refine_service.registry.max_open = 1
    first = await refine_service.begin()
    await refine_service.propose(first.run_id, CALL_EDGE)
    await refine_service.begin()
    assert (await refine_service.index.runs.prune_skipped_runs(0)).removed_runs == 1
    assert await refine_service.index.runs.run(first.run_id) is None
    assert await refine_service.index.refinements.of_run(first.run_id) == []


async def test_pruning_keeps_a_skipped_run_that_owns_a_live_refinement(
    refine_service: RefinementService,
):
    """A rejection is a record of a refusal and goes with its run; anything still live is graph
    state, and deleting its run would orphan it (Invariant 2)."""
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    committed = await refine_service.commit(run.run_id)
    await refine_service.index.runs.finish_run(
        run.run_id, RunOutcome(status=RunStatus.SKIPPED, error="assessment only")
    )
    assert (await refine_service.index.runs.prune_skipped_runs(0)).removed_runs == 0
    kept = await refine_service.index.refinements.refinement(
        committed.committed[0].refinement_id
    )
    assert kept is not None


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
    accepted = await refine_service.ledger.accept(result.committed[0].refinement_id)
    assert accepted.status is RefinementStatus.ACTIVE
    summary = await refine_service.rebuild()
    assert summary.refined == 1


async def test_a_second_identical_proposal_commits_as_a_confirmation(
    refine_service: RefinementService,
):
    first = await refine_service.begin()
    await refine_service.propose(first.run_id, CALL_EDGE)
    committed = await refine_service.commit(first.run_id)
    await refine_service.ledger.accept(committed.committed[0].refinement_id)
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


#: one out-of-scope proposal per kind, so the scope guard is pinned for all eight (spec 5.4)
_OUT_OF_SCOPE = {
    RefinementKind.ADD_EDGE: RefinementTarget(
        src="other.py::f", dst="other.py::g", edge_kind=EdgeKind.CALLS, name="g"
    ),
    RefinementKind.RETARGET_EDGE: RefinementTarget(
        src="other.py::f",
        from_dst="other.py::g",
        to_dst="other.py::h",
        edge_kind=EdgeKind.CALLS,
        name="g",
    ),
    RefinementKind.CONFIRM_EDGE: RefinementTarget(
        src="other.py::f", dst="other.py::g", edge_kind=EdgeKind.CALLS, name="g"
    ),
    RefinementKind.RESOLVE_AMBIGUOUS: RefinementTarget(
        node_id="other.py::f", name="g", edge_kind=EdgeKind.CALLS
    ),
    RefinementKind.RELABEL_CLUSTER: RefinementTarget(members=("other.py::f",)),
    RefinementKind.MOVE_NODE: RefinementTarget(
        node_id="other.py::f", members=("other.py::g",)
    ),
    RefinementKind.ANNOTATE_NODE: RefinementTarget(node_id="other.py::f"),
    RefinementKind.UNRESOLVABLE: RefinementTarget(node_id="other.py::f", name="g"),
}
_SCOPE_PAYLOAD = RefinementPayload(
    label="a cluster", annotation="a note", candidate="other.py::h"
)


@pytest.mark.parametrize("kind", sorted(_OUT_OF_SCOPE, key=lambda k: k.value))
async def test_every_kind_obeys_the_run_scope(
    refine_service: RefinementService, kind: RefinementKind
):
    """`covers` read three id fields, and `relabel_cluster` fills none of them: it named no id the
    guard looked at, so `all([])` put it in scope for every run at once."""
    run = await refine_service.begin(scope="svc.py")
    verdict = await refine_service.propose(
        run.run_id,
        Proposal(
            kind=kind,
            target=_OUT_OF_SCOPE[kind],
            payload=_SCOPE_PAYLOAD,
            reason="out of this run's scope",
        ),
    )
    assert verdict.outcome is ProposalOutcome.REJECTED
    assert verdict.refusal is RefusalKind.OUT_OF_SCOPE


async def test_a_scope_matches_on_a_path_boundary(refine_service: RefinementService):
    """A bare prefix match puts `svc.py` under the scope `svc`, a directory this repo does not
    have, and everything in `auditor/graphql/` under `auditor/graph`. The id has to fall on a path
    or symbol boundary, so this stages under a prefix match and is refused under the real rule."""
    run = await refine_service.begin(scope="svc")
    inside_the_prefix = NOTE.model_copy(
        update={"target": RefinementTarget(node_id="svc.py::load_user")}
    )
    verdict = await refine_service.propose(run.run_id, inside_the_prefix)
    assert verdict.outcome is ProposalOutcome.REJECTED
    assert verdict.refusal is RefusalKind.OUT_OF_SCOPE


@pytest.mark.parametrize("scope", ["/svc.py", "../svc.py", "a/../../b"])
async def test_a_scope_that_could_never_match_is_refused_at_begin(
    refine_service: RefinementService, scope: str
):
    """Node ids are relative to the checkout, so such a scope refuses every proposal the run makes
    and says nothing about why."""
    with pytest.raises(RefinementRefused, match="repo-relative"):
        await refine_service.begin(scope=scope)


async def test_a_trailing_separator_does_not_change_a_scope(
    refine_service: RefinementService,
):
    run = await refine_service.begin(scope="impl.py/")
    assert (await refine_service.propose(run.run_id, NOTE)).outcome is (
        ProposalOutcome.STAGED
    )


async def test_status_reports_what_is_staged_here(refine_service: RefinementService):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    report = await refine_service.status(run.run_id)
    assert report.staged_here is True
    assert [v.kind for v in report.staged] == [RefinementKind.ADD_EDGE]
    assert report.committed == ()


async def test_the_run_report_separates_what_committed_from_what_was_refused(
    refine_service: RefinementService,
):
    """`of_run` returns rejections too, so reading it straight into `committed` reported rows a
    run never committed, on a run that committed nothing at all."""
    run = await refine_service.begin()
    refused = await refine_service.propose(run.run_id, UNCALLED)
    await refine_service.propose(run.run_id, CALL_EDGE)
    open_report = await refine_service.status(run.run_id)
    assert open_report.committed == () and open_report.rejected == (
        refused.refinement_id,
    )
    result = await refine_service.commit(run.run_id)
    done = await refine_service.status(run.run_id)
    assert done.committed == (result.committed[0].refinement_id,)
    assert done.rejected == (refused.refinement_id,)


async def test_two_services_in_one_process_share_the_run_that_was_staged(
    refine_service: RefinementService, process_runs: dict[str, RunRegistry]
):
    """An MCP server builds a `RefinementService` per tool call: without one home per identity
    `graph_refine_propose` cannot find what `graph_refine_begin` staged."""
    parts = (refine_service.index, refine_service.root, refine_service.settings)
    opener = RefinementService(*parts, refine_service.user)
    proposer = RefinementService(*parts, refine_service.user)
    shared = RunRegistry.process(refine_service.identity)
    assert opener.registry is shared and proposer.registry is shared
    assert set(process_runs) == {refine_service.identity}
    run = await opener.begin()
    verdict = await proposer.propose(run.run_id, CALL_EDGE)
    assert verdict.outcome is ProposalOutcome.STAGED
    assert (await proposer.status(run.run_id)).staged_here is True


async def test_begin_reads_git_off_the_event_loop(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """`git_output` shells out twice with a 30 s timeout each; on the loop every other coroutine,
    which for an MCP server is every other tool call, waits behind it."""

    def slow(root, *args):
        time.sleep(0.2)
        return "abc"

    monkeypatch.setattr("auditor.graph.refine.service.git_output", slow)
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(tick())
    try:
        await refine_service.begin()
    finally:
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker
    assert ticks > 5


async def test_status_of_a_run_this_process_did_not_open_says_so(
    refine_service: RefinementService, refine_service_other: RefinementService
):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    report = await refine_service_other.status(run.run_id)
    assert report.staged_here is False
    assert report.staged == ()


_TRANSITIONS = {
    "accept": RefinementStatus.ACTIVE,
    "revert": RefinementStatus.REVERTED,
    "pin": RefinementStatus.PINNED,
}


@pytest.mark.parametrize("method", sorted(_TRANSITIONS))
async def test_the_hand_transitions(refine_service: RefinementService, method: str):
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    result = await refine_service.commit(run.run_id)
    moved = await getattr(refine_service.ledger, method)(
        result.committed[0].refinement_id
    )
    assert moved.status is _TRANSITIONS[method]


@pytest.mark.parametrize("method", sorted(_TRANSITIONS))
async def test_a_terminal_refinement_refuses_every_transition(
    refine_service: RefinementService, method: str
):
    """`rejected` is in none of the three allowed sets, so the claim is true today; widening any
    of them would have left this green while only `accept` was ever called."""
    run = await refine_service.begin()
    verdict = await refine_service.propose(run.run_id, UNCALLED)
    with pytest.raises(RefinementRefused, match="rejected"):
        await getattr(refine_service.ledger, method)(verdict.refinement_id)


async def test_prune_drops_only_the_skipped_runs_past_the_window(
    refine_service: RefinementService,
):
    """The one public method with no coverage; `skipped_retention_days` is the window it reads."""
    refine_service.user = UserSettings.model_validate(
        {"observer": {"skipped_retention_days": 0}}
    )
    kept = await refine_service.begin()
    refine_service.registry.max_open = 1
    await refine_service.begin()
    swept = await refine_service.prune()
    assert (swept.removed_runs, swept.stranded_runs) == (1, 0)
    assert await refine_service.index.runs.run(kept.run_id) is None
    remaining = await refine_service.index.runs.runs()
    assert [r.status for r in remaining] == [RunStatus.QUEUED]


async def test_prune_finishes_a_run_a_dead_process_left_open(
    refine_service: RefinementService,
):
    """`abort` is refused from every other process and the registry dies with the one that opened
    the run, so this row is reachable from nowhere else and no surface would ever show it done.

    The row is written with an old `started_at` rather than opened through `begin`: aging it by
    hand is the only way to have one, since no test can wait out the window.
    """
    stranded = await refine_service.index.runs.add_run(
        Run(
            repo_identity=refine_service.identity,
            status=RunStatus.QUEUED,
            started_at=time.time() - 7200,
        )
    )
    fresh = await refine_service.begin()
    swept = await refine_service.prune()
    assert swept.stranded_runs == 1
    dead = await refine_service.index.runs.run(stranded)
    assert dead is not None and dead.status is RunStatus.SKIPPED
    assert dead.error == "stranded: no commit within 3600 s"
    still_open = await refine_service.index.runs.run(fresh.run_id)
    assert still_open is not None and still_open.status is RunStatus.QUEUED


async def test_a_partition_prefix_reaches_every_stored_id(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """A caller names ids the way its own partition shows them; identity rows are toplevel
    relative. Every fixture has an empty prefix, so nothing here made the rebasing observable."""
    monkeypatch.setattr(type(refine_service), "prefix", property(lambda self: "sub/"))
    run = await refine_service.begin()
    assert (await refine_service.propose(run.run_id, CALL_EDGE)).outcome is (
        ProposalOutcome.STAGED
    )
    result = await refine_service.commit(run.run_id)
    rid = result.committed[0].refinement_id
    stored = await refine_service.ledger.refinement(rid)
    assert (stored.target.src, stored.target.dst) == (
        "sub/impl.py::Impl.run",
        "sub/svc.py::load_user",
    )
    anchors = await refine_service.index.refinements.anchors([rid])
    assert {a.node_id for a in anchors[rid]} == {
        "sub/impl.py::Impl.run",
        "sub/svc.py::load_user",
    }
    assert {a.path for a in anchors[rid]} == {"sub/impl.py", "sub/svc.py"}


async def test_an_unknown_refinement_id_is_named_not_ignored(
    refine_service: RefinementService,
):
    with pytest.raises(RefinementRefused, match="4242"):
        await refine_service.ledger.accept(4242)


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


def note(index: int) -> Proposal:
    """One of a batch of distinct staging proposals: same node, different annotation."""
    return NOTE.model_copy(
        update={"payload": RefinementPayload(annotation=f"the entry point {index}")}
    )


def slow_facts(monkeypatch: pytest.MonkeyPatch, delay: float = 0.05) -> None:
    """Force a real await between `_refused`'s cap read and the append that depends on it."""
    real = ProposalFacts.of.__func__

    async def slowly(cls, *args, **kwargs):
        await asyncio.sleep(delay)
        return await real(cls, *args, **kwargs)

    monkeypatch.setattr(ProposalFacts, "of", classmethod(slowly))


async def test_concurrent_proposes_admit_exactly_the_cap(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """`_refused` reads the staged count against the cap and then awaits the database twice before
    it appends. Without the run's lock every concurrent proposal passes a cap of three."""
    cap = 3
    refine_service.user = UserSettings.model_validate(
        {"observer": {"limits": {"max_changes_per_run": cap}}}
    )
    slow_facts(monkeypatch)
    run = await refine_service.begin()
    verdicts = await asyncio.gather(
        *(refine_service.propose(run.run_id, note(i)) for i in range(cap + 3))
    )
    staged = [v for v in verdicts if v.outcome is ProposalOutcome.STAGED]
    over_cap = [v for v in verdicts if v.refusal is RefusalKind.OVER_CAP]
    assert (len(staged), len(over_cap)) == (cap, 3)
    assert len((await refine_service.status(run.run_id)).staged) == cap


async def test_a_commit_waits_for_a_propose_already_in_flight(
    refine_service: RefinementService, monkeypatch: pytest.MonkeyPatch
):
    """The run's lock is the only thing holding the commit back: without it the commit reads the
    staged list, lands one row, and the in-flight proposal appends to a run nobody will insert."""
    slow_facts(monkeypatch)
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    in_flight = asyncio.create_task(refine_service.propose(run.run_id, NOTE))
    await asyncio.sleep(0)  # let it take the lock and reach its await
    result = await refine_service.commit(run.run_id)
    assert (await in_flight).outcome is ProposalOutcome.STAGED
    assert result.landed == 2
    assert len(await refine_service.index.refinements.of_run(run.run_id)) == 2


async def test_an_abort_racing_a_commit_cannot_overwrite_it(
    refine_service: RefinementService,
):
    """Both terminal methods close the run before their first real await, so the loser is refused
    by name rather than stamping `aborted` over a run that committed."""
    run = await refine_service.begin()
    await refine_service.propose(run.run_id, CALL_EDGE)
    result, aborted = await asyncio.gather(
        refine_service.commit(run.run_id),
        refine_service.abort(run.run_id, "the agent changed its mind"),
        return_exceptions=True,
    )
    assert not isinstance(result, BaseException) and result.landed == 1
    assert isinstance(aborted, RefinementRefused)
    finished = await refine_service.index.runs.run(run.run_id)
    assert finished is not None and finished.status is RunStatus.SUCCEEDED


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
