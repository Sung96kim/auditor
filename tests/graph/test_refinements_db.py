"""The identity-keyed refinement tables: what survives a repo being forgotten, what a status
transition writes, and what the foreign keys refuse."""

import sqlite3

import pytest

from auditor.database import IndexStore
from auditor.graph.model import EdgeKind
from auditor.graph.refine.models import (
    Anchor,
    ClientKind,
    EvalRow,
    ProducerKind,
    Refinement,
    RefinementKind,
    RefinementOutcome,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunnerKind,
    RunStatus,
    Tier,
    TriggerKind,
    TuningRow,
    TuningStatus,
)
from auditor.partition import Partition

IDENTITY = "/checkout/.git"


@pytest.fixture
async def refine_store(tmp_path):
    part = Partition(identity=IDENTITY, prefix="")
    store = await IndexStore.connect(tmp_path / "i.db", "/checkout", part)
    yield store
    await store.aclose()


def _run(**kw) -> Run:
    """A queued run for this identity. `started_at` is overridable, so it cannot be positional."""
    return Run(repo_identity=IDENTITY, **{"started_at": 100.0, **kw})


def _refinement(run_id: str, **kw) -> Refinement:
    return Refinement(
        run_id=run_id,
        repo_identity=IDENTITY,
        kind=kw.pop("kind", RefinementKind.ADD_EDGE),
        target=kw.pop(
            "target",
            RefinementTarget(src="m.py::f", dst="s.py::g", edge_kind=EdgeKind.CALLS),
        ),
        created_at=100.0,
        status_at=100.0,
        **kw,
    )


async def test_a_run_round_trips_every_column(refine_store):
    run = _run(
        client=ClientKind.CLAUDE_CODE,
        producer=ProducerKind.OBSERVER,
        runner=RunnerKind.CLAUDE,
        trigger_kind=TriggerKind.EDIT,
        trigger_detail={"files": ["m.py"]},
        session_id="s1",
        branch="main",
        commit_sha="abc123",
        dirty=True,
        model="haiku",
        prompt="look at m.py",
        tool_trace=[{"tool": "Read", "ts": 1.0}],
    )
    run_id = await refine_store.refinements.add_run(run)
    stored = await refine_store.refinements.run(run_id)
    assert stored == run  # every field, no lossy column


async def test_runs_filter_by_status_and_come_back_newest_first(refine_store):
    await refine_store.refinements.add_run(_run(status=RunStatus.SKIPPED))
    later = await refine_store.refinements.add_run(
        _run(status=RunStatus.SUCCEEDED, started_at=200.0)
    )
    assert [r.run_id for r in await refine_store.refinements.runs()][0] == later
    only = await refine_store.refinements.runs(statuses=[RunStatus.SKIPPED])
    assert [r.status for r in only] == [RunStatus.SKIPPED]


async def test_finish_run_records_the_terminal_state(refine_store):
    run_id = await refine_store.refinements.add_run(_run(status=RunStatus.RUNNING))
    await refine_store.refinements.finish_run(
        run_id,
        status=RunStatus.SUCCEEDED,
        summary="added one edge",
        cost_usd=0.004,
        num_turns=3,
        finished_at=150.0,
    )
    stored = await refine_store.refinements.run(run_id)
    assert (stored.status, stored.summary, stored.num_turns) == (
        RunStatus.SUCCEEDED,
        "added one edge",
        3,
    )
    assert stored.cost_usd == pytest.approx(0.004)
    assert stored.finished_at == 150.0


async def test_a_refinement_round_trips_with_its_anchors(refine_store):
    run_id = await refine_store.refinements.add_run(_run())
    anchors = (
        Anchor(path="m.py", node_id="m.py::f", truth_sha="t1", file_sha="f1"),
        Anchor(path="s.py", node_id="s.py::g", truth_sha="t2", file_sha="f2"),
    )
    rid = await refine_store.refinements.add_refinement(_refinement(run_id), anchors)
    (stored,) = await refine_store.refinements.refinements()
    assert stored.refinement_id == rid
    assert stored.target.src == "m.py::f"
    assert stored.target.edge_kind is EdgeKind.CALLS
    assert (
        stored.tier is Tier.C
    )  # the default: nothing auto-activates without an eval row
    got = await refine_store.refinements.anchors([rid])
    assert {a.node_id for a in got[rid]} == {"m.py::f", "s.py::g"}
    assert all(a.refinement_id == rid for a in got[rid])


async def test_active_returns_active_and_pinned_only(refine_store):
    run_id = await refine_store.refinements.add_run(_run())
    for status in RefinementStatus:
        await refine_store.refinements.add_refinement(
            _refinement(run_id, status=status)
        )
    statuses = {r.status for r in await refine_store.refinements.active()}
    assert statuses == {RefinementStatus.ACTIVE, RefinementStatus.PINNED}


async def test_set_status_stamps_status_at(refine_store):
    run_id = await refine_store.refinements.add_run(_run())
    rid = await refine_store.refinements.add_refinement(_refinement(run_id))
    await refine_store.refinements.set_status(rid, RefinementStatus.STALE, now=500.0)
    (stored,) = await refine_store.refinements.refinements()
    assert (stored.status, stored.status_at) == (RefinementStatus.STALE, 500.0)
    assert stored.created_at == 100.0  # untouched


async def test_apply_outcomes_writes_noop_and_drift_without_a_status(refine_store):
    run_id = await refine_store.refinements.add_run(_run())
    rid = await refine_store.refinements.add_refinement(
        _refinement(run_id, status=RefinementStatus.ACTIVE)
    )
    await refine_store.refinements.apply_outcomes(
        [RefinementOutcome(refinement_id=rid, noop_builds=2, drifted=True)], now=300.0
    )
    (stored,) = await refine_store.refinements.refinements()
    assert (stored.noop_builds, stored.drifted) == (2, True)
    assert (
        stored.status is RefinementStatus.ACTIVE
    )  # None means "leave the status alone"
    assert stored.status_at == 100.0


async def test_forgetting_the_repo_keeps_the_identity_rows(refine_store):
    """No REPO_FK on these tables, so another worktree's work survives `repos.forget()`."""
    run_id = await refine_store.refinements.add_run(_run())
    await refine_store.refinements.add_refinement(_refinement(run_id))
    await refine_store.repos.register(1.0)
    assert await refine_store.repos.forget() is True
    assert len(await refine_store.refinements.refinements()) == 1


async def test_identity_scopes_the_reads(tmp_path):
    db = tmp_path / "i.db"
    async with await IndexStore.connect(db, "/a", Partition(identity="/a/.git")) as a:
        await a.refinements.add_run(Run(repo_identity="/a/.git", started_at=1.0))
    async with await IndexStore.connect(db, "/b", Partition(identity="/b/.git")) as b:
        assert await b.refinements.runs() == []


async def test_a_refinement_needs_a_real_run(refine_store):
    """Invariant 2: every refinement links to a run, so the foreign key is enforced."""
    with pytest.raises(sqlite3.IntegrityError):
        await refine_store.refinements.add_refinement(_refinement("no-such-run"))


async def test_deleting_a_refinement_cascades_to_its_anchors(refine_store):
    run_id = await refine_store.refinements.add_run(_run())
    rid = await refine_store.refinements.add_refinement(
        _refinement(run_id),
        (Anchor(path="m.py", node_id="m.py::f", truth_sha="t1", file_sha="f1"),),
    )
    await refine_store._worker.run(
        lambda c: c.execute(
            "DELETE FROM graph_refinements WHERE refinement_id = ?", (rid,)
        )
    )
    assert await refine_store.refinements.anchors([rid]) == {}


def _tuning(run_id: str, **kw) -> TuningRow:
    return TuningRow(
        repo_identity=IDENTITY,
        run_id=run_id,
        key=kw.pop("key", "graph.knn_k"),
        value_json=kw.pop("value_json", "12"),
        created_at=100.0,
        **kw,
    )


def _eval(**kw) -> EvalRow:
    return EvalRow(
        repo_identity=IDENTITY,
        runner=RunnerKind.CLAUDE,
        model="haiku",
        suite=kw.pop("suite", "add"),
        stratum=kw.pop("stratum", "bare"),
        n=20,
        correct=19,
        precision=0.95,
        recall=0.8,
        lower_bound_95=0.78,
        created_at=100.0,
        **kw,
    )


async def test_a_tuning_row_round_trips_and_filters_by_status(refine_store):
    run_id = await refine_store.refinements.add_run(_run())
    tid = await refine_store.refinements.add_tuning(_tuning(run_id))
    await refine_store.refinements.add_tuning(
        _tuning(run_id, key="graph.cluster_floor", status=TuningStatus.REJECTED)
    )
    (stored,) = await refine_store.refinements.tuning(statuses=[TuningStatus.PENDING])
    assert (stored.tuning_id, stored.key, stored.value_json) == (
        tid,
        "graph.knn_k",
        "12",
    )
    assert len(await refine_store.refinements.tuning()) == 2


async def test_set_tuning_status_moves_one_row(refine_store):
    run_id = await refine_store.refinements.add_run(_run())
    tid = await refine_store.refinements.add_tuning(_tuning(run_id))
    await refine_store.refinements.set_tuning_status(tid, TuningStatus.ACTIVE)
    (stored,) = await refine_store.refinements.tuning()
    assert stored.status is TuningStatus.ACTIVE


async def test_eval_rows_filter_by_runner_and_model(refine_store):
    await refine_store.refinements.add_eval(_eval())
    await refine_store.refinements.add_eval(_eval(suite="retarget"))
    assert len(await refine_store.refinements.evals()) == 2
    hit = await refine_store.refinements.evals(runner=RunnerKind.CLAUDE, model="haiku")
    assert {row.suite for row in hit} == {"add", "retarget"}
    assert await refine_store.refinements.evals(model="sonnet") == []


async def test_tuning_and_evals_survive_a_forgotten_repo(refine_store):
    run_id = await refine_store.refinements.add_run(_run())
    await refine_store.refinements.add_tuning(_tuning(run_id))
    await refine_store.refinements.add_eval(_eval())
    await refine_store.repos.register(1.0)
    await refine_store.repos.forget()
    assert len(await refine_store.refinements.tuning()) == 1
    assert len(await refine_store.refinements.evals()) == 1


async def test_prune_skipped_runs_spares_real_runs_and_recent_ones(refine_store):
    old_skipped = await refine_store.refinements.add_run(
        _run(status=RunStatus.SKIPPED, started_at=0.0)
    )
    await refine_store.refinements.add_run(
        _run(status=RunStatus.SKIPPED, started_at=1_000_000.0)
    )
    await refine_store.refinements.add_run(
        _run(status=RunStatus.SUCCEEDED, started_at=0.0)
    )
    removed = await refine_store.refinements.prune_skipped_runs(7, now=1_000_000.0)
    assert removed == 1
    kept = {r.run_id for r in await refine_store.refinements.runs()}
    assert old_skipped not in kept
    assert len(kept) == 2


@pytest.mark.parametrize("child", ["refinement", "tuning"])
async def test_prune_never_orphans_a_child_row(refine_store, child):
    """Both child tables carry `run_id REFERENCES graph_runs (run_id)` with no ON DELETE, so a
    sweep that deleted a referenced run would raise IntegrityError inside the daemon's idle tick."""
    run_id = await refine_store.refinements.add_run(
        _run(status=RunStatus.SKIPPED, started_at=0.0)
    )
    if child == "refinement":
        await refine_store.refinements.add_refinement(_refinement(run_id))
    else:
        await refine_store.refinements.add_tuning(_tuning(run_id))
    assert await refine_store.refinements.prune_skipped_runs(7, now=1_000_000.0) == 0
    assert await refine_store.refinements.run(run_id) is not None
