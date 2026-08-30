"""The target-driven run: a brief from chosen pairs, the `running` stamp and the commit guards."""

import pytest

from auditor.graph.refine.models import (
    Checkout,
    ClientKind,
    NodePair,
    ProducerKind,
    RunStatus,
    TriggerDetail,
    TriggerKind,
)
from auditor.graph.refine.runner import FakeRun, FakeRunner, RefinementJob
from auditor.graph.refine.service import RefinementRefused, RefinementService

_PAIR = NodePair(node_id="impl.py::Impl.run", name="load_user")
_NOTE = {
    "kind": "annotate_node",
    "reason": "a note the guard has to survive",
    "target": {"node_id": "impl.py::Impl.run"},
    "payload": {"annotation": "seen"},
}


async def test_a_run_opened_with_targets_is_briefed_on_exactly_those_pairs(
    refine_service: RefinementService,
):
    """C22: the brief a target-driven run reads is scoped to the pairs, never to a path prefix."""
    run = await refine_service.begin(
        producer=ProducerKind.OBSERVER,
        trigger=TriggerKind.EDIT,
        detail=TriggerDetail(files=("impl.py",), targets=(_PAIR,)),
    )
    brief = await refine_service.brief(run.run_id)
    assert [(t.node_id, t.name) for t in brief.targets] == [(_PAIR.node_id, _PAIR.name)]
    assert brief.scope == ""


async def test_a_run_with_no_targets_still_gets_the_scope_s_own_queue(
    refine_service: RefinementService,
):
    """The shipped path is untouched: `graph refine <scope>` still reads the queue by prefix."""
    run = await refine_service.begin(scope="")
    brief = await refine_service.brief(run.run_id)
    assert brief.targets != ()


async def test_the_targeted_read_keeps_the_order_the_loop_ranked(
    refine_service: RefinementService,
):
    """`GraphDB.unresolved` orders by priority; the loop's own ordering is the one that decides."""
    rows = await refine_service.facts.queue(None, limit=None, external=True)
    pairs = tuple(NodePair(node_id=row.node_id, name=row.name) for row in rows)
    reversed_pairs = tuple(reversed(pairs))
    got = await refine_service.facts.targeted(reversed_pairs)
    assert [(r.node_id, r.name) for r in got] == [
        (p.node_id, p.name) for p in reversed_pairs
    ]


async def test_a_pair_no_row_answers_is_simply_absent(
    refine_service: RefinementService,
):
    """A queue rebuilt between the choice and the brief can drop a row; that is not an error."""
    assert (
        await refine_service.facts.targeted((NodePair(node_id="x.py::y", name="z"),))
        == []
    )
    assert await refine_service.facts.targeted(()) == []


async def test_a_runner_stamps_its_row_running_before_it_calls_a_model(
    refine_service: RefinementService,
):
    """C54: `RunStatus.RUNNING` had no writer at all before this slice."""
    seen: list[RunStatus] = []

    async def watching(run_id: str, proposal):
        stored = await refine_service.index.runs.run(run_id)
        seen.append(stored.status)
        return await refine_service.propose(run_id, proposal)

    runner = FakeRunner(
        refine_service, proposer=watching, pretend=FakeRun(script=(_NOTE,))
    )
    product = await runner.run(
        RefinementJob(client=ClientKind.CLI, producer=ProducerKind.CLI)
    )
    assert seen == [RunStatus.RUNNING]
    stored = await refine_service.index.runs.run(product.run.run_id)
    assert stored.status is RunStatus.SUCCEEDED


async def test_a_job_hands_its_detail_checkout_and_dirtiness_to_the_row(
    refine_service: RefinementService,
):
    """Spec 8.5's three pre-run reads travel on the job, so the runner does not re-shell for them."""
    runner = FakeRunner(refine_service)
    product = await runner.run(
        RefinementJob(
            trigger=TriggerKind.SUSPECT,
            producer=ProducerKind.OBSERVER,
            detail=TriggerDetail(targets=(_PAIR,)),
            checkout=Checkout(branch="side", commit_sha="deadbeef"),
            dirty=True,
        )
    )
    stored = await refine_service.index.runs.run(product.run.run_id)
    assert (stored.branch, stored.commit_sha) == ("side", "deadbeef")
    assert stored.dirty is True
    assert stored.trigger_detail.targets == (_PAIR,)
    assert stored.trigger_kind is TriggerKind.SUSPECT


async def test_a_commit_is_refused_when_an_anchor_s_facts_moved_under_it(
    refine_service: RefinementService,
):
    """C50: `_land_all` re-read HEAD and nothing else before this slice."""
    run = await refine_service.begin(scope="")
    await refine_service.brief(run.run_id)
    verdict = await refine_service.propose(run.run_id, _NOTE)
    assert verdict.outcome.value == "staged"
    (refine_service.root / "impl.py").write_text(
        "from base import Base\nclass Impl(Base):\n    def run(self):\n"
        "        return _local()\n\ndef _local():\n    return 1\n"
    )
    with pytest.raises(RefinementRefused, match="the facts moved under this run"):
        await refine_service.commit(run.run_id)
    stored = await refine_service.index.runs.run(run.run_id)
    assert stored.status is RunStatus.REJECTED
    assert await refine_service.index.refinements.count() == 0


async def test_a_commit_whose_anchors_held_still_lands(
    refine_service: RefinementService,
):
    """The guard must not refuse the ordinary case: nothing touched the file."""
    runner = FakeRunner(refine_service, pretend=FakeRun(script=(_NOTE,)))
    product = await runner.run(RefinementJob())
    assert product.landed is not None
    assert await refine_service.index.refinements.count() == 1


async def test_a_run_that_staged_nothing_lands_without_a_rebuild(
    refine_service: RefinementService,
):
    """No insert, no lock, no rebuild: the guard has to stay off the empty path (spec 6)."""
    runner = FakeRunner(refine_service)
    product = await runner.run(RefinementJob())
    assert product.landed is not None and product.landed.rebuilt is False
