"""The identity-keyed stores: what survives a repo being forgotten, what a status transition
writes, what the foreign keys refuse, and what another checkout's identity cannot reach."""

import json
import sqlite3
from collections.abc import Sequence

import pytest

from auditor.database import IndexStore
from auditor.database.refinements import NoSuchRun
from auditor.graph.model import EdgeKind
from auditor.graph.refine.models import (
    Anchor,
    ClientKind,
    EvalMetrics,
    EvalRow,
    ProducerKind,
    Refinement,
    RefinementKind,
    RefinementOutcome,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunnerKind,
    RunOutcome,
    RunStatus,
    RunUsage,
    Spend,
    Stratum,
    Tier,
    ToolCall,
    TriggerDetail,
    TriggerKind,
    TuningRow,
    TuningStatus,
)
from auditor.models import Partition

IDENTITY = "/checkout/.git"
OTHER = "/elsewhere/.git"


@pytest.fixture
async def refine_store(tmp_path):
    part = Partition(identity=IDENTITY, prefix="")
    store = await IndexStore.connect(tmp_path / "i.db", "/checkout", part)
    yield store
    await store.aclose()


@pytest.fixture
async def other_store(tmp_path, refine_store):
    """A second handle on the same database, bound to a different checkout's identity."""
    store = await IndexStore.connect(
        tmp_path / "i.db", "/elsewhere", Partition(identity=OTHER)
    )
    yield store
    await store.aclose()


async def _write_raw(
    store: IndexStore, sql: str, params: Sequence[object] = ()
) -> None:
    """Plant a row no writer would, in the one place this file reaches for a raw connection."""

    def op(conn: sqlite3.Connection) -> None:
        conn.execute(sql, params)
        conn.commit()

    await store._worker.run(op)


def _run(**kw) -> Run:
    """A queued run for this identity. `started_at` is overridable, so it cannot be positional."""
    return Run(repo_identity=IDENTITY, **{"started_at": 100.0, **kw})


def _saturated_run() -> Run:
    """A run with every field at a distinct non-default value, so a dropped or transposed column
    changes the round-trip result."""
    return Run(
        run_id="run-1",
        repo_identity=IDENTITY,
        origin_partition="/checkout",
        partition_prefix="apps/backend/",
        client=ClientKind.CLAUDE_CODE,
        producer=ProducerKind.OBSERVER,
        runner=RunnerKind.CLAUDE,
        trigger_kind=TriggerKind.EDIT,
        trigger_detail=TriggerDetail(files=("m.py",), reason="edited"),
        session_id="s1",
        agent_name="refiner",
        branch="main",
        commit_sha="abc123",
        dirty=True,
        model="haiku",
        prompt="look at m.py",
        system_prompt_sha="sha-1",
        tool_trace=(ToolCall(tool="Read", ts=1.0, detail="m.py"),),
        usage=RunUsage(
            cost_usd=0.25,
            cost_estimated=True,
            input_tokens=11,
            output_tokens=22,
            num_turns=3,
        ),
        sdk_session_id="sdk-1",
        status=RunStatus.SUCCEEDED,
        summary="added one edge",
        error="none",
        started_at=100.0,
        finished_at=150.0,
    )


def _refinement(run_id: str, **kw) -> Refinement:
    return Refinement(
        run_id=run_id,
        repo_identity=kw.pop("repo_identity", IDENTITY),
        kind=kw.pop("kind", RefinementKind.ADD_EDGE),
        reason=kw.pop("reason", "the call resolves there"),
        target=kw.pop(
            "target",
            RefinementTarget(
                src="m.py::f", dst="s.py::g", edge_kind=EdgeKind.CALLS, name="g"
            ),
        ),
        created_at=kw.pop("created_at", 100.0),
        status_at=kw.pop("status_at", 100.0),
        **kw,
    )


def _saturated_refinement(run_id: str) -> Refinement:
    """Every field at a distinct non-default value (see `_saturated_run`)."""
    return Refinement(
        run_id=run_id,
        repo_identity=IDENTITY,
        kind=RefinementKind.RETARGET_EDGE,
        target=RefinementTarget(
            src="m.py::f",
            from_dst="s.py::g",
            to_dst="s.py::h",
            edge_kind=EdgeKind.CALLS,
            name="g",
        ),
        payload={"reason_code": "shadowed"},
        reason="the resolver picked the wrong g",
        evidence=({"path": "m.py", "line": 12, "excerpt": "g()"},),
        confidence=0.75,
        tier=Tier.A,
        status=RefinementStatus.ACTIVE,
        drifted=True,
        noop_builds=2,
        supersedes=41,
        attempts=3,
        created_at=100.0,
        status_at=140.0,
    )


def _tuning(run_id: str, **kw) -> TuningRow:
    return TuningRow(
        repo_identity=IDENTITY,
        run_id=run_id,
        key=kw.pop("key", "graph.knn_k"),
        value_json=kw.pop("value_json", "12"),
        created_at=100.0,
        **kw,
    )


def _saturated_tuning(run_id: str) -> TuningRow:
    return TuningRow(
        repo_identity=IDENTITY,
        run_id=run_id,
        key="graph.knn_k",
        value_json="12",
        token="tok-1",
        reason="recall is low on bare calls",
        status=TuningStatus.ACTIVE,
        metrics=_metrics(),
        created_at=100.0,
    )


def _metrics(lower: float = 0.78) -> EvalMetrics:
    """Every metric at a distinct non-default value; ``lower`` is what a gate test varies."""
    return EvalMetrics(
        n=20,
        correct=19,
        precision=0.95,
        recall=0.8,
        false_add_rate=0.05,
        false_removal_rate=0.02,
        lower_bound_95=lower,
    )


def _eval(**kw) -> EvalRow:
    return EvalRow(
        repo_identity=IDENTITY,
        runner=RunnerKind.CLAUDE,
        model=kw.pop("model", "haiku"),
        suite=kw.pop("suite", "add"),
        stratum=kw.pop("stratum", Stratum.ALL),
        metrics=kw.pop("metrics", _metrics()),
        created_at=kw.pop("created_at", 100.0),
        **kw,
    )


def _saturated_eval() -> EvalRow:
    return EvalRow(
        repo_identity=IDENTITY,
        runner=RunnerKind.CLAUDE,
        model="haiku",
        suite="add",
        stratum=Stratum.ALL,
        metrics=_metrics(),
        cost_usd=0.5,
        num_turns=4,
        created_at=100.0,
    )


@pytest.mark.parametrize(
    "model, saturated, assigned",
    [
        (Run, _saturated_run, ()),
        (Refinement, lambda: _saturated_refinement("run-1"), ("refinement_id",)),
        (TuningRow, lambda: _saturated_tuning("run-1"), ("tuning_id",)),
        (EvalRow, _saturated_eval, ("eval_id",)),
    ],
    ids=["run", "refinement", "tuning", "eval"],
)
def test_the_saturated_fixture_leaves_no_field_at_its_default(
    model, saturated, assigned
):
    """Guards the guard: a field added to the model without a value in the fixture would let the
    round-trip test pass with that column dropped or transposed. ``assigned`` is the id the insert
    hands back, the one field a fixture cannot set."""
    filled = set(saturated().model_dump(exclude_defaults=True))
    assert set(model.model_fields) - set(assigned) == filled


async def _round_trip_run(index, _run_id: str) -> None:
    run = _saturated_run()
    assert await index.runs.run(await index.runs.add_run(run)) == run


async def _round_trip_refinement(index, run_id: str) -> None:
    refinement = _saturated_refinement(run_id)
    rid = await index.refinements.add_refinement(refinement)
    (stored,) = await index.refinements.refinements()
    assert stored == refinement.model_copy(update={"refinement_id": rid})


async def _round_trip_tuning(index, run_id: str) -> None:
    row = _saturated_tuning(run_id)
    tid = await index.tuning.add_tuning(row)
    (stored,) = await index.tuning.tuning()
    assert stored == row.model_copy(update={"tuning_id": tid})


async def _round_trip_eval(index, _run_id: str) -> None:
    row = _saturated_eval()
    eid = await index.evals.add_eval(row)
    (stored,) = await index.evals.latest(RunnerKind.CLAUDE, "haiku")
    assert stored == row.model_copy(update={"eval_id": eid})


#: table -> (facade attribute, a write-then-read of every column of a saturated row)
_ROUND_TRIPS = {
    "graph_runs": ("runs", _round_trip_run),
    "graph_refinements": ("refinements", _round_trip_refinement),
    "graph_tuning": ("tuning", _round_trip_tuning),
    "graph_evals": ("evals", _round_trip_eval),
}


@pytest.mark.parametrize("table_name", list(_ROUND_TRIPS))
async def test_a_row_round_trips_every_column(refine_store, table_name):
    """Every field distinct and non-default, so a dropped or transposed column changes the
    result rather than comparing equal by coincidence."""
    run_id = await refine_store.runs.add_run(_run())
    await _ROUND_TRIPS[table_name][1](refine_store, run_id)


@pytest.mark.parametrize(
    "store_attr, table_name",
    [
        ("runs", "graph_runs"),
        ("refinements", "graph_refinements"),
        ("refinements", "graph_refinement_anchors"),
        ("tuning", "graph_tuning"),
        ("evals", "graph_evals"),
        ("findings", "findings"),
    ],
)
def test_every_insert_binds_its_columns_by_name(refine_store, store_attr, table_name):
    """The binds are ordered by the declaration, and a mapping that does not match it is refused
    by name rather than written transposed."""
    store = getattr(refine_store, store_attr)
    columns = store.TABLES[table_name].insert_columns()
    values = dict.fromkeys(columns, "x")
    sql, binds = store.insert_sql(table_name, values)
    assert sql.startswith(f"INSERT INTO {table_name} ({', '.join(columns)})")
    assert binds == tuple("x" for _ in columns)
    with pytest.raises(KeyError, match=columns[0]):
        store.insert_sql(table_name, {k: "x" for k in columns[1:]})


def _swap_columns(monkeypatch, store, table_name: str, left: str, right: str) -> None:
    """Swap two same-typed columns in a live declaration, for the length of one test."""
    table = store.TABLES[table_name]
    order = {c.name: i for i, c in enumerate(table.cols)}
    cols = list(table.cols)
    cols[order[left]], cols[order[right]] = cols[order[right]], cols[order[left]]
    monkeypatch.setitem(
        store.TABLES, table_name, table.model_copy(update={"cols": tuple(cols)})
    )


@pytest.mark.parametrize(
    "table_name, left, right",
    [
        ("graph_runs", "summary", "error"),
        ("graph_refinements", "target", "payload"),
        ("graph_tuning", "key", "value_json"),
        ("graph_evals", "precision", "recall"),
    ],
)
async def test_a_reordered_declaration_still_round_trips(
    refine_store, monkeypatch, table_name, left, right
):
    """Reordering two same-typed columns used to transpose every later row in silence: the values
    were a hand-ordered tuple while the column list was derived. Now both come from one mapping,
    so the row lands under the right names either way."""
    store_attr, round_trip = _ROUND_TRIPS[table_name]
    run_id = await refine_store.runs.add_run(_run())
    _swap_columns(
        monkeypatch, getattr(refine_store, store_attr), table_name, left, right
    )
    await round_trip(refine_store, run_id)


async def test_runs_filter_by_status_and_come_back_newest_first(refine_store):
    await refine_store.runs.add_run(_run(status=RunStatus.SKIPPED))
    later = await refine_store.runs.add_run(
        _run(status=RunStatus.SUCCEEDED, started_at=200.0)
    )
    assert [r.run_id for r in await refine_store.runs.runs()][0] == later
    only = await refine_store.runs.runs(statuses=[RunStatus.SKIPPED])
    assert [r.status for r in only] == [RunStatus.SKIPPED]


async def test_runs_limit_counts_rows_the_caller_sees(refine_store):
    for started in (100.0, 200.0, 300.0):
        await refine_store.runs.add_run(_run(started_at=started))
    assert [r.started_at for r in await refine_store.runs.runs(limit=2)] == [
        300.0,
        200.0,
    ]


async def test_finish_run_records_the_terminal_state(refine_store):
    run_id = await refine_store.runs.add_run(_run(status=RunStatus.RUNNING))
    await refine_store.runs.finish_run(
        run_id,
        RunOutcome(
            status=RunStatus.SUCCEEDED,
            summary="added one edge",
            usage=RunUsage(cost_usd=0.004, num_turns=3),
            finished_at=150.0,
        ),
    )
    stored = await refine_store.runs.run(run_id)
    assert (stored.status, stored.summary, stored.usage.num_turns) == (
        RunStatus.SUCCEEDED,
        "added one edge",
        3,
    )
    assert stored.usage.cost_usd == pytest.approx(0.004)
    assert stored.finished_at == 150.0


async def test_record_prompt_stores_the_brief_and_the_rules_hash(refine_store):
    run_id = await refine_store.runs.add_run(_run())
    await refine_store.runs.record_prompt(
        run_id, prompt="brief text", system_prompt_sha="ab" * 32
    )
    stored = await refine_store.runs.run(run_id)
    assert (stored.prompt, stored.system_prompt_sha) == ("brief text", "ab" * 32)


async def test_record_prompt_overwrites_an_earlier_brief(refine_store):
    """A run that re-briefs itself keeps the prompt it was last given, not the first one."""
    run_id = await refine_store.runs.add_run(_run())
    await refine_store.runs.record_prompt(
        run_id, prompt="first", system_prompt_sha="a" * 64
    )
    await refine_store.runs.record_prompt(
        run_id, prompt="second", system_prompt_sha="b" * 64
    )
    stored = await refine_store.runs.run(run_id)
    assert (stored.prompt, stored.system_prompt_sha) == ("second", "b" * 64)


async def test_record_prompt_refuses_an_unknown_run(refine_store):
    """A lost prompt would be invisible, so a write that matched no row refuses by name."""
    with pytest.raises(NoSuchRun, match="no run nope on this checkout"):
        await refine_store.runs.record_prompt(
            "nope", prompt="brief", system_prompt_sha="c" * 64
        )


async def test_record_prompt_cannot_stamp_another_checkouts_run(
    refine_store, other_store
):
    """The identity tables are shared, so an unpartitioned UPDATE would cross checkouts."""
    run_id = await refine_store.runs.add_run(_run(prompt="mine"))
    with pytest.raises(NoSuchRun):
        await other_store.runs.record_prompt(
            run_id, prompt="theirs", system_prompt_sha="d" * 64
        )
    assert (await refine_store.runs.run(run_id)).prompt == "mine"


async def test_finish_run_stamps_a_time_when_the_outcome_does_not(refine_store):
    run_id = await refine_store.runs.add_run(_run(status=RunStatus.RUNNING))
    await refine_store.runs.finish_run(run_id, RunOutcome(status=RunStatus.ABORTED))
    stored = await refine_store.runs.run(run_id)
    assert stored.finished_at > 0


async def test_a_refinement_round_trips_with_its_anchors(refine_store):
    run_id = await refine_store.runs.add_run(_run())
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
    run_id = await refine_store.runs.add_run(_run())
    for status in RefinementStatus:
        await refine_store.refinements.add_refinement(
            _refinement(run_id, status=status)
        )
    statuses = {r.status for r in await refine_store.refinements.active()}
    assert statuses == {RefinementStatus.ACTIVE, RefinementStatus.PINNED}


async def test_refinements_filter_on_status_and_kind_together(refine_store):
    run_id = await refine_store.runs.add_run(_run())
    await refine_store.refinements.add_refinement(
        _refinement(run_id, status=RefinementStatus.ACTIVE)
    )
    await refine_store.refinements.add_refinement(
        _refinement(
            run_id,
            status=RefinementStatus.ACTIVE,
            kind=RefinementKind.ANNOTATE_NODE,
            target=RefinementTarget(node_id="m.py::f"),
            payload={"annotation": "entry point"},
        )
    )
    await refine_store.refinements.add_refinement(
        _refinement(run_id, status=RefinementStatus.STALE)
    )
    hit = await refine_store.refinements.refinements(
        statuses=[RefinementStatus.ACTIVE], kinds=[RefinementKind.ADD_EDGE]
    )
    assert [(r.status, r.kind) for r in hit] == [
        (RefinementStatus.ACTIVE, RefinementKind.ADD_EDGE)
    ]


async def test_set_status_stamps_status_at(refine_store):
    run_id = await refine_store.runs.add_run(_run())
    rid = await refine_store.refinements.add_refinement(_refinement(run_id))
    await refine_store.refinements.set_status(rid, RefinementStatus.STALE, now=500.0)
    (stored,) = await refine_store.refinements.refinements()
    assert (stored.status, stored.status_at) == (RefinementStatus.STALE, 500.0)
    assert stored.created_at == 100.0  # untouched


async def test_apply_outcomes_writes_noop_and_drift_without_a_status(refine_store):
    run_id = await refine_store.runs.add_run(_run())
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


async def test_write_outcomes_lands_inside_the_build_transaction(refine_store):
    """`GraphWrite.apply` calls it on the open connection, so the graph and its provenance land
    together or not at all."""
    run_id = await refine_store.runs.add_run(_run())
    rid = await refine_store.refinements.add_refinement(
        _refinement(run_id, status=RefinementStatus.ACTIVE)
    )
    outcome = RefinementOutcome(
        refinement_id=rid, status=RefinementStatus.STALE, noop_builds=3
    )
    with pytest.raises(RuntimeError, match="build failed"):
        await refine_store.transaction(
            lambda conn: _fail_after(
                refine_store.refinements.write_outcomes(conn, [outcome], 300.0)
            )
        )
    (stored,) = await refine_store.refinements.refinements()
    assert (stored.status, stored.noop_builds) == (RefinementStatus.ACTIVE, 0)

    await refine_store.transaction(
        lambda conn: refine_store.refinements.write_outcomes(conn, [outcome], 300.0)
    )
    (stored,) = await refine_store.refinements.refinements()
    assert (stored.status, stored.noop_builds, stored.status_at) == (
        RefinementStatus.STALE,
        3,
        300.0,
    )


def _fail_after(_written: None) -> None:
    raise RuntimeError("build failed")


async def test_forgetting_the_repo_keeps_the_identity_rows(refine_store):
    """No REPO_FK on these tables, so another worktree's work survives `repos.forget()`."""
    run_id = await refine_store.runs.add_run(_run())
    await refine_store.refinements.add_refinement(_refinement(run_id))
    await refine_store.repos.register(1.0)
    assert await refine_store.repos.forget() is True
    assert len(await refine_store.refinements.refinements()) == 1


async def test_identity_scopes_the_reads(tmp_path):
    db = tmp_path / "i.db"
    async with await IndexStore.connect(db, "/a", Partition(identity="/a/.git")) as a:
        await a.runs.add_run(Run(repo_identity="/a/.git", started_at=1.0))
    async with await IndexStore.connect(db, "/b", Partition(identity="/b/.git")) as b:
        assert await b.runs.runs() == []


async def test_another_identity_cannot_move_these_rows(refine_store, other_store):
    """Every write addresses a globally unique id, so it has to bind the identity as well or a
    second checkout could stale, finish or retune rows it can never read."""
    run_id = await refine_store.runs.add_run(_run(status=RunStatus.RUNNING))
    rid = await refine_store.refinements.add_refinement(
        _refinement(run_id, status=RefinementStatus.ACTIVE),
        (Anchor(path="m.py", node_id="m.py::f", truth_sha="t1"),),
    )
    tid = await refine_store.tuning.add_tuning(_tuning(run_id))

    with pytest.raises(NoSuchRun):
        await other_store.runs.finish_run(run_id, RunOutcome(status=RunStatus.FAILED))
    await other_store.refinements.set_status(rid, RefinementStatus.REJECTED)
    await other_store.refinements.apply_outcomes(
        [RefinementOutcome(refinement_id=rid, status=RefinementStatus.STALE)]
    )
    await other_store.tuning.set_tuning_status(tid, TuningStatus.REJECTED)

    assert await other_store.refinements.anchors([rid]) == {}
    assert (await refine_store.runs.run(run_id)).status is RunStatus.RUNNING
    (refinement,) = await refine_store.refinements.refinements()
    assert refinement.status is RefinementStatus.ACTIVE
    (tuning,) = await refine_store.tuning.tuning()
    assert tuning.status is TuningStatus.PENDING


async def test_a_refinement_needs_a_real_run(refine_store):
    """Invariant 2: every refinement links to a run, so the foreign key is enforced."""
    with pytest.raises(sqlite3.IntegrityError):
        await refine_store.refinements.add_refinement(_refinement("no-such-run"))


async def test_deleting_a_refinement_cascades_to_its_anchors(refine_store):
    run_id = await refine_store.runs.add_run(_run())
    rid = await refine_store.refinements.add_refinement(
        _refinement(run_id),
        (Anchor(path="m.py", node_id="m.py::f", truth_sha="t1", file_sha="f1"),),
    )
    await _write_raw(
        refine_store, "DELETE FROM graph_refinements WHERE refinement_id = ?", (rid,)
    )
    assert await refine_store.refinements.anchors([rid]) == {}


async def test_a_tuning_row_round_trips_and_filters_by_status(refine_store):
    run_id = await refine_store.runs.add_run(_run())
    tid = await refine_store.tuning.add_tuning(_tuning(run_id))
    await refine_store.tuning.add_tuning(
        _tuning(run_id, key="graph.cluster_floor", status=TuningStatus.REJECTED)
    )
    (stored,) = await refine_store.tuning.tuning(statuses=[TuningStatus.PENDING])
    assert (stored.tuning_id, stored.key, stored.value_json) == (
        tid,
        "graph.knn_k",
        "12",
    )
    assert len(await refine_store.tuning.tuning()) == 2


async def test_set_tuning_status_moves_one_row(refine_store):
    run_id = await refine_store.runs.add_run(_run())
    tid = await refine_store.tuning.add_tuning(_tuning(run_id))
    await refine_store.tuning.set_tuning_status(tid, TuningStatus.ACTIVE)
    (stored,) = await refine_store.tuning.tuning()
    assert stored.status is TuningStatus.ACTIVE


async def test_eval_rows_are_bound_to_their_runner_and_model(refine_store):
    """`latest` is the one reader: a row of another model must not answer this model's gate."""
    await refine_store.evals.add_eval(_eval())
    await refine_store.evals.add_eval(_eval(suite="retarget"))
    hit = await refine_store.evals.latest(RunnerKind.CLAUDE, "haiku")
    assert {row.suite for row in hit} == {"add", "retarget"}
    assert await refine_store.evals.latest(RunnerKind.CLAUDE, "sonnet") == []


async def test_a_stored_row_this_build_cannot_read_is_dropped_not_raised(refine_store):
    """`service._policy` is on the `propose` path, where one bad row would take down every
    correction rather than one measurement."""
    await refine_store.evals.add_eval(_eval())
    await refine_store.evals.add_eval(_eval(suite="retarget"))

    await _write_raw(
        refine_store, "UPDATE graph_evals SET stratum = 'nonsense' WHERE suite = 'add'"
    )
    rows = await refine_store.evals.latest(RunnerKind.CLAUDE, "haiku")
    assert [row.suite for row in rows] == ["retarget"]


async def test_latest_keeps_only_the_newest_row_per_suite_and_stratum(refine_store):
    """P1: `graph_evals` has no unique key, so the reader is what makes a regression count."""
    await refine_store.evals.add_eval(_eval(created_at=100.0, metrics=_metrics(0.99)))
    await refine_store.evals.add_eval(_eval(created_at=200.0, metrics=_metrics(0.40)))
    rows = await refine_store.evals.latest(RunnerKind.CLAUDE, "haiku")
    assert [row.metrics.lower_bound_95 for row in rows] == [0.40]


async def test_latest_reads_created_at_not_insertion_order(refine_store):
    """The older row inserted second must not win: the newest row is the newest, not the last."""
    await refine_store.evals.add_eval(_eval(created_at=200.0, metrics=_metrics(0.99)))
    await refine_store.evals.add_eval(_eval(created_at=100.0, metrics=_metrics(0.40)))
    (row,) = await refine_store.evals.latest(RunnerKind.CLAUDE, "haiku")
    assert row.metrics.lower_bound_95 == 0.99


async def test_latest_breaks_a_tie_on_eval_id(refine_store):
    await refine_store.evals.add_eval(_eval(created_at=100.0, metrics=_metrics(0.99)))
    await refine_store.evals.add_eval(_eval(created_at=100.0, metrics=_metrics(0.40)))
    (row,) = await refine_store.evals.latest(RunnerKind.CLAUDE, "haiku")
    assert row.metrics.lower_bound_95 == 0.40


async def test_latest_answers_one_row_per_key_and_binds_runner_and_model(refine_store):
    for suite in ("add", "collision"):
        await refine_store.evals.add_eval(_eval(suite=suite))
        await refine_store.evals.add_eval(_eval(suite=suite))
    await refine_store.evals.add_eval(_eval(suite="add", model="sonnet"))
    rows = await refine_store.evals.latest(RunnerKind.CLAUDE, "haiku")
    assert [(row.suite, row.stratum) for row in rows] == [
        ("add", Stratum.ALL),
        ("collision", Stratum.ALL),
    ]
    assert await refine_store.evals.latest(RunnerKind.FAKE, "haiku") == []


async def test_latest_keeps_the_strata_apart(refine_store):
    for stratum in (Stratum.SAME_MODULE, Stratum.NEITHER):
        await refine_store.evals.add_eval(_eval(stratum=stratum))
    rows = await refine_store.evals.latest(RunnerKind.CLAUDE, "haiku")
    assert {row.stratum for row in rows} == {Stratum.SAME_MODULE, Stratum.NEITHER}


async def test_tuning_and_evals_survive_a_forgotten_repo(refine_store):
    run_id = await refine_store.runs.add_run(_run())
    await refine_store.tuning.add_tuning(_tuning(run_id))
    await refine_store.evals.add_eval(_eval())
    await refine_store.repos.register(1.0)
    await refine_store.repos.forget()
    assert len(await refine_store.tuning.tuning()) == 1
    assert len(await refine_store.evals.latest(RunnerKind.CLAUDE, "haiku")) == 1


async def test_runs_can_exclude_a_status_from_the_default_view(refine_store):
    """The default `graph log` view excludes `skipped`. With no row of that status in any test,
    deleting the clause left the suite green and three assessment runs leaked into the view."""
    kept = await refine_store.runs.add_run(_run(status=RunStatus.SUCCEEDED))
    await refine_store.runs.add_run(_run(status=RunStatus.SKIPPED))
    everything = await refine_store.runs.runs()
    assert len(everything) == 2
    visible = await refine_store.runs.runs(exclude=[RunStatus.SKIPPED])
    assert [r.run_id for r in visible] == [kept]
    assert await refine_store.runs.count(exclude=[RunStatus.SKIPPED]) == 1


async def test_runs_apply_their_time_window_and_count_the_same_rows(refine_store):
    await refine_store.runs.add_run(_run(started_at=100.0))
    recent = await refine_store.runs.add_run(_run(started_at=900.0))
    assert [r.run_id for r in await refine_store.runs.runs(since=500.0)] == [recent]
    assert await refine_store.runs.count(since=500.0) == 1
    assert await refine_store.runs.count() == 2


async def test_the_newest_first_order_is_the_one_the_window_filters_on(refine_store):
    """`since` filters `created_at`, so the page has to order on it too: ordered by id alone, a
    backdated row pages ahead of rows inside the window."""
    run_id = await refine_store.runs.add_run(_run())
    old_but_last = await refine_store.refinements.add_refinement(
        _refinement(run_id, created_at=10.0)
    )
    newest = await refine_store.refinements.add_refinement(
        _refinement(run_id, created_at=900.0)
    )
    page = await refine_store.refinements.refinements(newest_first=True)
    assert [r.refinement_id for r in page] == [newest, old_but_last]
    oldest_first = await refine_store.refinements.refinements()
    assert [r.refinement_id for r in oldest_first] == [old_but_last, newest]


async def test_the_time_window_runs_before_the_limit_not_after_it(refine_store):
    """The window is in SQL on purpose. Moved into Python after the `LIMIT`, an oldest-first page
    of a busy repo fetches fifty ancient rows and drops every one of them: 5 becomes 0."""
    run_id = await refine_store.runs.add_run(_run())
    for _ in range(55):
        await refine_store.refinements.add_refinement(
            _refinement(run_id, created_at=100.0)
        )
    for _ in range(5):
        await refine_store.refinements.add_refinement(
            _refinement(run_id, created_at=900.0)
        )
    inside = await refine_store.refinements.refinements(
        since=500.0, newest_first=False, limit=50
    )
    assert len(inside) == 5
    assert await refine_store.refinements.count(since=500.0) == 5
    assert await refine_store.refinements.count() == 60


async def test_counts_by_run_splits_what_a_run_kept_from_what_it_refused(refine_store):
    """The run log's last column. Returning `{}` was invisible: nothing asserted a count."""
    mine = await refine_store.runs.add_run(_run())
    other = await refine_store.runs.add_run(_run())
    await refine_store.refinements.add_refinement(_refinement(mine))
    await refine_store.refinements.add_refinement(
        _refinement(mine, status=RefinementStatus.REJECTED)
    )
    await refine_store.refinements.add_refinement(
        _refinement(mine, status=RefinementStatus.REJECTED)
    )
    counts = await refine_store.refinements.counts_by_run([mine, other])
    assert (counts[mine].committed, counts[mine].rejected) == (1, 2)
    assert counts[mine].total == 3
    assert counts[mine].summary == "1 committed, 2 rejected"
    assert other not in counts
    assert await refine_store.refinements.counts_by_run([]) == {}


async def test_prune_skipped_runs_spares_real_runs_and_recent_ones(refine_store):
    old_skipped = await refine_store.runs.add_run(
        _run(status=RunStatus.SKIPPED, started_at=0.0)
    )
    await refine_store.runs.add_run(
        _run(status=RunStatus.SKIPPED, started_at=1_000_000.0)
    )
    await refine_store.runs.add_run(_run(status=RunStatus.SUCCEEDED, started_at=0.0))
    swept = await refine_store.runs.prune_skipped_runs(7, now=1_000_000.0)
    assert (swept.removed_runs, swept.removed_refinements) == (1, 0)
    kept = {r.run_id for r in await refine_store.runs.runs()}
    assert old_skipped not in kept
    assert len(kept) == 2


async def test_prune_counts_the_rejections_it_deletes_with_the_run(refine_store):
    """The command's own help promised nothing live is deleted, and reported runs only: a caller
    told "1 run removed" could not see the two rows that went with it."""
    run_id = await refine_store.runs.add_run(
        _run(status=RunStatus.SKIPPED, started_at=0.0)
    )
    for _ in range(2):
        await refine_store.refinements.add_refinement(
            _refinement(run_id, status=RefinementStatus.REJECTED)
        )
    swept = await refine_store.runs.prune_skipped_runs(7, now=1_000_000.0)
    assert (swept.removed_runs, swept.removed_refinements, swept.stranded_runs) == (
        1,
        2,
        0,
    )
    assert await refine_store.refinements.of_run(run_id) == []


async def test_a_run_left_queued_by_a_dead_process_is_finished(refine_store):
    """A registry is process-local, so nothing else can ever close such a run. The row is aged by
    hand because that is the only way to have one: no test can outlive the window.
    """
    stranded = await refine_store.runs.add_run(_run(started_at=0.0))
    fresh = await refine_store.runs.add_run(_run(started_at=999_000.0))
    finished = await refine_store.runs.finish_stranded_runs(
        older_than=3600, now=1_000_000.0
    )
    assert finished == 1
    dead = await refine_store.runs.run(stranded)
    assert dead is not None and dead.status is RunStatus.SKIPPED
    assert dead.error == "stranded: no commit within 3600 s"
    assert dead.finished_at == 1_000_000.0
    alive = await refine_store.runs.run(fresh)
    assert alive is not None and alive.status is RunStatus.QUEUED


async def test_a_stranded_sweep_leaves_a_finished_run_alone(refine_store):
    """Only `queued` is reachable from nowhere: a run that ended keeps the ending it recorded."""
    done = await refine_store.runs.add_run(
        _run(status=RunStatus.SUCCEEDED, started_at=0.0)
    )
    assert await refine_store.runs.finish_stranded_runs(older_than=1, now=1_000.0) == 0
    kept = await refine_store.runs.run(done)
    assert kept is not None and kept.status is RunStatus.SUCCEEDED


@pytest.mark.parametrize("child", ["refinement", "tuning"])
async def test_prune_never_orphans_a_child_row(refine_store, child):
    """Both child tables carry `run_id REFERENCES graph_runs (run_id)` with no ON DELETE, so a
    sweep that deleted a referenced run would raise IntegrityError inside the daemon's idle tick."""
    run_id = await refine_store.runs.add_run(
        _run(status=RunStatus.SKIPPED, started_at=0.0)
    )
    if child == "refinement":
        await refine_store.refinements.add_refinement(_refinement(run_id))
    else:
        await refine_store.tuning.add_tuning(_tuning(run_id))
    assert (
        await refine_store.runs.prune_skipped_runs(7, now=1_000_000.0)
    ).removed_runs == 0
    assert await refine_store.runs.run(run_id) is not None


async def test_a_row_written_outside_the_text_rules_still_reads_back(refine_store):
    """The read path is lenient by context, so a row hand-written before a rule existed, or by
    another tool, does not make every build fail on it."""
    run_id = await refine_store.runs.add_run(_run())
    rid = await refine_store.refinements.add_refinement(_refinement(run_id), ())
    await _write_raw(
        refine_store,
        "UPDATE graph_refinements SET payload = ?, reason = '' WHERE refinement_id = ?",
        (json.dumps({"annotation": "x" * 400}), rid),
    )
    (stored,) = await refine_store.refinements.refinements()
    assert len(stored.payload.annotation or "") == 400
    assert stored.reason == ""


async def test_finish_run_refuses_an_unknown_run(refine_store):
    """The sibling of `record_prompt`: a terminal stamp that matched no row leaves a run open
    somewhere and nothing at all said so."""
    with pytest.raises(NoSuchRun, match="no run nope on this checkout"):
        await refine_store.runs.finish_run("nope", RunOutcome(status=RunStatus.FAILED))


async def test_spend_since_sums_only_the_runs_that_called_a_model(graph_store):
    """An assessment-only row spent nothing, so it consumes neither ceiling (P2)."""
    identity = graph_store.partition.identity
    for runner, cost, started in (
        (RunnerKind.CLAUDE, 0.10, 100.0),
        (RunnerKind.CLAUDE, 0.05, 200.0),
        (RunnerKind.NONE, 0.0, 210.0),
        (RunnerKind.CLAUDE, 9.99, 10.0),
    ):
        await graph_store.runs.add_run(
            Run(
                repo_identity=identity,
                runner=runner,
                started_at=started,
                usage=RunUsage(cost_usd=cost),
            )
        )
    spend = await graph_store.runs.spend_since(50.0)
    assert spend.runs == 2
    assert spend.cost_usd == pytest.approx(0.15)


async def test_spend_since_on_an_empty_window_is_zero_not_none(graph_store):
    assert await graph_store.runs.spend_since(0.0) == Spend()
