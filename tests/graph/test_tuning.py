"""Spec 11's knob tuning: the allow-list, the per-token stopword model, the precedence read,
the trial's metrics and guards, and the proposal lifecycle."""

import asyncio
import json
import threading
from pathlib import Path

import pytest
from _support import tool_data
from fastmcp import Client
from fastmcp.exceptions import ToolError

from auditor.cli.helpers import open_index
from auditor.config import AuditorSettings, GraphConfig, load_config
from auditor.graph.build import GraphBuilder, tuned_settings
from auditor.graph.refine.models import (
    Refinement,
    RefinementKind,
    RefinementOutcome,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunnerKind,
    RunStatus,
    TriggerKind,
    TuningBaseline,
    TuningMetrics,
    TuningRow,
    TuningStatus,
)
from auditor.graph.refine.service import RefinementService, RunRegistry
from auditor.graph.refine.trial import (
    NO_GRAPH,
    TuningService,
    _guard,
    _stranded_pins,
    baseline_of,
    measured,
)
from auditor.graph.refine.tuning import (
    TOKEN_LENGTH,
    TUNING_KNOBS,
    TuningLedger,
    TuningRefused,
    active_stopwords,
    candidate_stopwords,
    confirmation_token,
    knob,
    stopword,
    tuned,
)
from auditor.mcp import mcp
from auditor.user_settings import UserSettings


def _row(token: str, status: TuningStatus = TuningStatus.ACTIVE) -> TuningRow:
    return TuningRow(
        repo_identity="i",
        key="stopwords",
        value_json=json.dumps(token),
        run_id="r",
        status=status,
    )


def _pin(
    rid: int,
    kind: RefinementKind,
    status: RefinementStatus = RefinementStatus.PINNED,
    noop_builds: int = 0,
) -> Refinement:
    relabel = kind is RefinementKind.RELABEL_CLUSTER
    return Refinement(
        refinement_id=rid,
        run_id="r",
        repo_identity="i",
        kind=kind,
        reason="pinned",
        target=RefinementTarget(
            node_id=None if relabel else "m.py::a", members=("a", "b")
        ),
        payload=RefinementPayload(label="widgets") if relabel else RefinementPayload(),
        status=status,
        noop_builds=noop_builds,
    )


def test_the_allow_list_is_the_four_keys_the_spec_names():
    """Spec 11 names four; S11 ships one and the other three stay declared so enabling one is a
    flag flip rather than a new table."""
    assert set(TUNING_KNOBS) == {
        "stopwords",
        "name_similarity_threshold",
        "cluster_floor",
        "knn_k",
    }
    assert {k for k, v in TUNING_KNOBS.items() if v.shipped} == {"stopwords"}
    assert [
        TUNING_KNOBS[k].low for k in ("name_similarity_threshold", "cluster_floor")
    ] == [0.2, 0.2]
    assert [
        TUNING_KNOBS[k].high for k in ("name_similarity_threshold", "cluster_floor")
    ] == [0.8, 0.8]
    assert (TUNING_KNOBS["knn_k"].low, TUNING_KNOBS["knn_k"].high) == (4, 16)


@pytest.mark.parametrize(
    ("key", "fragment"),
    [
        ("god_concept_sigma", "not tunable"),
        ("knn_k", "not shipped"),
        ("cluster_floor", "not shipped"),
        ("name_similarity_threshold", "not shipped"),
    ],
)
def test_a_key_outside_the_shipped_allow_list_is_refused_by_name(
    key: str, fragment: str
):
    """A knob that is off the list and one that is on it but deferred fail differently, because
    only one of them is a typo."""
    with pytest.raises(TuningRefused) as raised:
        knob(key)
    assert fragment in str(raised.value)


def test_stopwords_is_the_one_shipped_knob():
    assert knob("stopwords").field == "stopwords"


@pytest.mark.parametrize(
    "value", ["Widget", " widget ", "widget"], ids=["cased", "padded", "plain"]
)
def test_a_stopword_token_is_normalized(value: str):
    assert stopword(value) == "widget"


@pytest.mark.parametrize(
    "value",
    ["", "9lives", "two words", "a" * 41, "-dash", 7, ["widget"]],
    ids=["empty", "digit-first", "spaced", "too-long", "dashed", "int", "list"],
)
def test_a_value_that_is_not_one_token_is_refused(value: object):
    """Spec 11 tunes stopwords per token, so a list is as wrong as a number: the row a proposal
    writes holds one word and revert takes exactly that word back out."""
    with pytest.raises(TuningRefused):
        stopword(value)


def test_only_active_rows_reach_the_config():
    rows = [
        _row("beta"),
        _row("alpha"),
        _row("gamma", TuningStatus.PENDING),
        _row("delta", TuningStatus.REVERTED),
        _row("alpha"),
    ]
    assert active_stopwords(rows) == ("alpha", "beta")


def test_a_trials_candidate_is_the_active_set_plus_the_one_token_it_adds():
    """The config the next build would produce, not the new token alone: once one token is
    active, measuring `[new]` compares a graph nobody will ever build (E2)."""
    rows = [_row("alpha"), _row("gamma", TuningStatus.PENDING)]
    assert candidate_stopwords(rows) == ("alpha",)
    assert candidate_stopwords(rows, ["beta"]) == ("alpha", "beta")
    assert candidate_stopwords(rows, ["alpha"]) == ("alpha",)


def test_active_rows_reach_the_build_when_the_repo_set_nothing():
    assert tuned(GraphConfig(), candidate_stopwords([_row("widget")])).stopwords == [
        "widget"
    ]


def test_repo_policy_beats_an_active_row(tmp_path):
    """Spec 11's precedence, and the reason it needs `model_fields_set`: a repo that wrote
    `stopwords = []` and a repo that wrote nothing validate to the same value."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.auditor]\n[tool.auditor.graph]\nstopwords = ['repo']\n"
    )
    cfg = load_config(tmp_path).graph
    assert "stopwords" in cfg.model_fields_set
    assert tuned(cfg, ("widget",)).stopwords == ["repo"]


def test_a_repo_that_set_another_graph_key_still_takes_the_tuning(tmp_path):
    """Precedence is per key: setting `cluster_floor` does not freeze `stopwords`."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.auditor]\n[tool.auditor.graph]\ncluster_floor = 0.5\n"
    )
    cfg = load_config(tmp_path).graph
    assert cfg.model_fields_set == {"cluster_floor"}
    assert tuned(cfg, ("widget",)).stopwords == ["widget"]


def test_no_active_row_leaves_the_config_object_alone():
    cfg = GraphConfig()
    assert tuned(cfg, candidate_stopwords([_row("x", TuningStatus.PENDING)])) is cfg


def test_the_confirmation_token_is_short_and_unambiguous():
    """No l, o or 0/1: a human retypes this from a terminal."""
    tokens = {confirmation_token() for _ in range(200)}
    assert len(tokens) > 190
    assert all(len(t) == TOKEN_LENGTH for t in tokens)
    assert not {c for t in tokens for c in t} & set("lo01")


async def test_the_build_reads_active_rows_and_a_pending_row_changes_nothing(
    facts_store,
):
    """The load-bearing half of accept: without this read a tuning row is a note in a table."""
    settings = AuditorSettings()
    run_id = await facts_store.runs.add_run(
        Run(repo_identity=facts_store.partition.identity, started_at=1.0)
    )
    await facts_store.tuning.add_tuning(
        TuningRow(
            repo_identity=facts_store.partition.identity,
            key="stopwords",
            value_json=json.dumps("loader"),
            run_id=run_id,
            status=TuningStatus.PENDING,
        )
    )
    assert (await tuned_settings(facts_store, settings)).graph.stopwords == []
    tuning_id = await facts_store.tuning.add_tuning(
        TuningRow(
            repo_identity=facts_store.partition.identity,
            key="stopwords",
            value_json=json.dumps("loader"),
            run_id=run_id,
            status=TuningStatus.ACTIVE,
        )
    )
    assert tuning_id > 0
    assert (await tuned_settings(facts_store, settings)).graph.stopwords == ["loader"]
    both = await tuned_settings(facts_store, settings, extra=("widget",))
    assert both.graph.stopwords == ["loader", "widget"]


async def test_a_build_with_no_tuning_row_hands_back_the_settings_it_was_given(
    facts_store,
):
    """The read costs one query and no copy on the overwhelmingly common path."""
    settings = AuditorSettings()
    assert await tuned_settings(facts_store, settings) is settings


async def _stored(store) -> dict[str, object]:
    """Every graph table a build writes, as a value, so a trial can be proved not to move one.

    `text_model` is S13-lite's stored tf-idf fit, and it belongs here because `extra_stopwords` is
    applied inside the fit (`naming.py:33`): a trial that leaked it would leave `graph search`
    ranking on a config nobody accepted.
    """
    return {
        "nodes": await store.graph.nodes(),
        "edges": await store.graph.all_edges(),
        "clusters": await store.graph.clusters(),
        "unresolved": await store.graph.unresolved(),
        "text_model": await store.graph.text_model(),
    }


async def test_a_trial_shapes_a_graph_and_writes_nothing(facts_store):
    """Invariant 6 and Invariant 1 together: a trial has to answer "what would this config do"
    without leaving the next reader looking at trial output."""
    settings = AuditorSettings()
    await GraphBuilder().run(facts_store, settings)
    before = await _stored(facts_store)
    trial_settings = await tuned_settings(facts_store, settings, extra=("load",))
    write = await GraphBuilder().shape(facts_store, trial_settings)
    assert write.nodes and write.clusters
    assert await _stored(facts_store) == before


async def test_shape_and_run_agree_on_what_the_build_produced(facts_store):
    """`run` is `shape` plus the write, so the two may not disagree about the graph."""
    settings = AuditorSettings()
    write = await GraphBuilder().shape(facts_store, settings)
    report = await GraphBuilder().run(facts_store, settings)
    assert (report.nodes, report.edges, report.clusters) == (
        len(write.nodes),
        len(write.edges),
        len(write.clusters),
    )


async def test_the_baseline_is_read_from_the_stored_graph(facts_store):
    """Spec 11 asks for two rebuilds; one of them is the graph already on disk (P5)."""
    settings = AuditorSettings()
    report = await GraphBuilder().run(facts_store, settings)
    base, name_edges, labels = await baseline_of(facts_store, settings.graph, [])
    assert base.clusters == report.clusters
    assert 0 <= base.top_cluster_share <= 1.0
    assert len(labels) == report.clusters
    assert name_edges == sum(
        1 for e in await facts_store.graph.all_edges() if e["kind"] == "name_similar"
    )


async def test_the_baseline_counts_the_pins_this_checkout_already_strands(facts_store):
    """The other half of E3: a pin the current build cannot place is on the baseline, so the
    delta a guard reads is the stopword's own doing."""
    settings = AuditorSettings()
    await GraphBuilder().run(facts_store, settings)
    already = _pin(7, RefinementKind.RELABEL_CLUSTER, noop_builds=2)
    base, _, _ = await baseline_of(facts_store, settings.graph, [already])
    assert base.stranded_pins == 1


async def test_a_trial_that_changes_nothing_measures_zero_churn(facts_store):
    """The control: the same config twice has to score identical, or every other number here is
    measuring the harness."""
    settings = AuditorSettings()
    await GraphBuilder().run(facts_store, settings)
    base, name_edges, labels = await baseline_of(facts_store, settings.graph, [])
    write = await GraphBuilder().shape(facts_store, settings)
    trial = measured(
        write, [], base, name_edges, labels, cfg=settings.graph, now=1_000.0
    )
    assert trial.passed
    assert trial.metrics.refused == ""
    assert trial.metrics.measured_at == 1_000.0
    assert trial.status is TuningStatus.PENDING
    assert trial.metrics.name_edge_churn == 0.0
    assert trial.metrics.label_churn == 0.0
    assert trial.metrics.clusters == base.clusters
    assert trial.metrics.modularity == pytest.approx(base.modularity)


@pytest.mark.parametrize(
    ("metrics", "fragment"),
    [
        (
            TuningMetrics(
                stranded_pins=1, clusters=10, baseline=TuningBaseline(clusters=10)
            ),
            "pinned cluster refinement",
        ),
        (
            TuningMetrics(clusters=13, baseline=TuningBaseline(clusters=10)),
            "outside the 20% band",
        ),
        (
            TuningMetrics(
                clusters=10,
                singletons=3,
                baseline=TuningBaseline(clusters=10, singletons=2),
            ),
            "singleton clusters 2 -> 3",
        ),
        (
            TuningMetrics(
                clusters=10,
                top_cluster_share=0.4,
                baseline=TuningBaseline(clusters=10, top_cluster_share=0.3),
            ),
            "top cluster share",
        ),
    ],
    ids=["stranded-pin", "cluster-band", "singletons-up", "top-share-up"],
)
def test_each_spec_guard_refuses_by_name(metrics: TuningMetrics, fragment: str):
    """Four guards, four messages: a human reading `graph tuning list` has to know which one."""
    assert fragment in _guard(metrics)


@pytest.mark.parametrize("clusters", [8, 12], ids=["low-edge", "high-edge"])
def test_the_band_is_inclusive_at_its_edges(clusters: int):
    """20% of 10 is 2, so 8 and 12 pass and 7 and 13 do not."""
    assert (
        _guard(TuningMetrics(clusters=clusters, baseline=TuningBaseline(clusters=10)))
        == ""
    )


def test_only_a_pin_this_clustering_lost_counts_as_stranded():
    """Differential and exact: a pin the checkout's own build already strands cannot blame the
    stopword, and a verdict from triage is not a strand at all (E3)."""
    lost = _pin(1, RefinementKind.RELABEL_CLUSTER)
    already = _pin(2, RefinementKind.MOVE_NODE, noop_builds=1)
    triaged = _pin(3, RefinementKind.RELABEL_CLUSTER)
    loose = _pin(4, RefinementKind.RELABEL_CLUSTER, status=RefinementStatus.ACTIVE)
    outcomes = (
        RefinementOutcome(refinement_id=1, applied=False, noop_builds=1),
        RefinementOutcome(refinement_id=2, applied=False, noop_builds=1),
        RefinementOutcome(
            refinement_id=3,
            applied=False,
            noop_builds=0,
            status=RefinementStatus.REDUNDANT,
        ),
        RefinementOutcome(refinement_id=4, applied=False, noop_builds=1),
    )
    assert _stranded_pins(outcomes, [lost, already, triaged, loose]) == 1


@pytest.fixture
async def tuning(refine_service: RefinementService) -> TuningService:
    """A tuning service over the three-file fixture repo, with tuning left at its default mode."""
    return TuningService(service=refine_service)


def _service_with(refine_service: RefinementService, user: dict) -> RefinementService:
    """The same index and checkout under a different user overlay, which is the only way a test
    can change `observer.tuning`: the service holds the one settings object (M-6, H1)."""
    return RefinementService(
        refine_service.index,
        refine_service.root,
        refine_service.settings,
        UserSettings.model_validate(user),
        registry=RunRegistry(),
    )


async def test_a_proposal_lands_pending_under_a_tune_run(tuning: TuningService):
    """Spec 8.3 item 5 and 9.2: a tuning proposal is a `graph_tuning` row under its own `tune`
    run, and it is not a refinement."""
    row = await tuning.propose("stopwords", "Loader", "loader is in every module name")
    assert (row.key, row.status) == ("stopwords", TuningStatus.PENDING)
    assert json.loads(row.value_json) == "loader"
    assert row.token and len(row.token) == TOKEN_LENGTH
    assert row.reason == "loader is in every module name"
    run = await tuning.service.index.runs.run(row.run_id)
    assert run is not None
    assert (run.trigger_kind, run.runner) == (TriggerKind.TUNE, RunnerKind.NONE)
    assert run.status is RunStatus.SUCCEEDED
    assert await tuning.service.index.refinements.of_run(row.run_id) == []


async def test_a_succeeded_tune_run_carries_its_note_as_a_summary(
    tuning: TuningService,
):
    """`graph log` paints `error` red and labels it "summary" in the detail view, so a run that
    ended well must not put its note there (H2)."""
    row = await tuning.propose("stopwords", "loader", "because")
    run = await tuning.service.index.runs.run(row.run_id)
    assert run is not None
    assert run.error is None
    assert run.summary == "tuning proposal for stopwords"


async def test_a_proposal_takes_no_slot_in_the_run_registry(tuning: TuningService):
    """A proposal stages nothing, so it has no business evicting a run that does (L8)."""
    await tuning.propose("stopwords", "loader", "because")
    assert tuning.service.registry.open_runs == {}


async def test_tuning_off_refuses_at_propose_time(refine_service: RefinementService):
    """Spec 11: `tuning = "off"` is checked before anything is written, so nothing is."""
    off = TuningService(
        service=_service_with(refine_service, {"observer": {"tuning": {"mode": "off"}}})
    )
    with pytest.raises(TuningRefused, match="tuning is off"):
        await off.propose("stopwords", "loader", "because")
    assert await off.ledger.rows() == []


@pytest.mark.parametrize(
    ("key", "value", "reason", "match"),
    [
        ("knn_k", 9, "because", "not shipped"),
        ("god_concept_sigma", 4.0, "because", "not tunable"),
        ("stopwords", ["a", "b"], "because", "one token"),
        ("stopwords", "loader", "  ", "needs a reason"),
    ],
    ids=["deferred-knob", "unknown-knob", "list-value", "no-reason"],
)
async def test_a_refused_proposal_writes_no_row(
    tuning: TuningService, key: str, value: object, reason: str, match: str
):
    """Spec 9.2's verifier is "allow-list + range", and a failed verify is not a row."""
    with pytest.raises(TuningRefused, match=match):
        await tuning.propose(key, value, reason)
    assert await tuning.ledger.rows() == []


async def test_one_proposal_per_key_per_day(tuning: TuningService):
    """Spec 11's rate limit, counted from the newest row for the key rather than the oldest."""
    await tuning.propose("stopwords", "loader", "one", now=1_000_000.0)
    with pytest.raises(TuningRefused, match="one stopwords proposal per day"):
        await tuning.propose("stopwords", "widget", "two", now=1_000_000.0 + 86_399.0)
    later = await tuning.propose(
        "stopwords", "widget", "two", now=1_000_000.0 + 86_401.0
    )
    assert later.status is TuningStatus.PENDING


async def test_a_reverted_proposal_stops_holding_the_key_for_the_day(
    tuning: TuningService,
):
    """A proposal taken back out has no claim on its key: the rate limit counts live rows (L4)."""
    first = await tuning.propose("stopwords", "loader", "one", now=1_000_000.0)
    await tuning.ledger.revert(str(first.tuning_id), token=first.token)
    second = await tuning.propose("stopwords", "widget", "two", now=1_000_000.0 + 60.0)
    assert second.status is TuningStatus.PENDING


async def test_a_second_proposal_for_the_same_token_supersedes_the_first(
    tuning: TuningService,
):
    """Spec 5.8's `superseded`: the older pending row keeps its reason and stops being a choice."""
    first = await tuning.propose("stopwords", "loader", "one", now=1.0)
    second = await tuning.propose("stopwords", "loader", "better reason", now=200_000.0)
    rows = {r.tuning_id: r.status for r in await tuning.ledger.rows()}
    assert rows[first.tuning_id] is TuningStatus.SUPERSEDED
    assert rows[second.tuning_id] is TuningStatus.PENDING


async def test_an_active_token_cannot_be_proposed_again(tuning: TuningService):
    row = await tuning.propose("stopwords", "loader", "one", now=1.0)
    await tuning.measure(row.tuning_id)
    await tuning.ledger.accept(str(row.tuning_id), token=row.token, cap=20)
    with pytest.raises(TuningRefused, match="already an active stopword"):
        await tuning.propose("stopwords", "loader", "again", now=200_000.0)


async def test_the_cap_is_the_configured_maximum(refine_service: RefinementService):
    """`stopwords_max` was declared in S1b and read nowhere until this slice."""
    capped = TuningService(
        service=_service_with(
            refine_service, {"observer": {"tuning": {"stopwords_max": 1}}}
        )
    )
    first = await capped.propose("stopwords", "loader", "one", now=1.0)
    await capped.measure(first.tuning_id)
    await capped.ledger.accept(str(first.tuning_id), token=first.token, cap=1)
    with pytest.raises(TuningRefused, match="the cap is 1"):
        await capped.propose("stopwords", "widget", "two", now=200_000.0)


async def test_accept_needs_the_stored_confirmation_word(tuning: TuningService):
    """Spec 12.2's `--token <word>`: the id alone activates nothing, because an id is guessable."""
    row = await tuning.propose("stopwords", "loader", "one")
    await tuning.measure(row.tuning_id)
    with pytest.raises(TuningRefused, match="wrong confirmation word"):
        await tuning.ledger.accept(str(row.tuning_id), token="nope42", cap=20)
    assert (await tuning.ledger.row(str(row.tuning_id))).status is TuningStatus.PENDING
    moved = await tuning.ledger.accept(str(row.tuning_id), token=row.token, cap=20)
    assert moved.status is TuningStatus.ACTIVE


async def test_an_unmeasured_row_cannot_be_accepted(tuning: TuningService):
    """Spec 11 says a trial records its metrics and lands pending; accepting a row no trial ever
    looked at would activate a knob nothing measured."""
    row = await tuning.propose("stopwords", "loader", "one")
    with pytest.raises(TuningRefused, match="has no trial yet"):
        await tuning.ledger.accept(str(row.tuning_id), token=row.token, cap=20)


async def test_revert_takes_an_active_row_back_out(tuning: TuningService):
    row = await tuning.propose("stopwords", "loader", "one")
    await tuning.measure(row.tuning_id)
    await tuning.ledger.accept(str(row.tuning_id), token=row.token, cap=20)
    reverted = await tuning.ledger.revert(str(row.tuning_id), token=row.token)
    assert reverted.status is TuningStatus.REVERTED
    assert reverted.reason == "one"
    with pytest.raises(TuningRefused, match="is reverted"):
        await tuning.ledger.revert(str(row.tuning_id), token=row.token)


async def test_a_row_is_named_by_id_or_by_the_token_it_proposes(tuning: TuningService):
    """Spec 12.2 writes `<id|key>`; `stopwords` names every row at once, so the second spelling
    is the word itself (P10)."""
    row = await tuning.propose("stopwords", "loader", "one")
    assert (await tuning.ledger.row("loader")).tuning_id == row.tuning_id
    assert (await tuning.ledger.row(str(row.tuning_id))).tuning_id == row.tuning_id
    with pytest.raises(TuningRefused, match="no tuning row 'widget'"):
        await tuning.ledger.row("widget")


async def test_an_accepted_row_reaches_the_next_build(tuning: TuningService):
    """The whole slice in one assertion: propose, measure, accept, and the build reads it."""
    row = await tuning.propose("stopwords", "loader", "one")
    await tuning.measure(row.tuning_id)
    before = await tuned_settings(tuning.service.index, tuning.service.settings)
    assert before.graph.stopwords == []
    await tuning.ledger.accept(str(row.tuning_id), token=row.token, cap=20)
    after = await tuned_settings(tuning.service.index, tuning.service.settings)
    assert after.graph.stopwords == ["loader"]


async def test_a_measured_trial_lands_pending_with_its_metrics(tuning: TuningService):
    """Spec 11's E1: measuring records, it does not apply (E2)."""
    row = await tuning.propose("stopwords", "load", "load is everywhere here")
    trial = await tuning.measure(row.tuning_id)
    stored = await tuning.ledger.row(str(row.tuning_id))
    assert stored.status is TuningStatus.PENDING
    assert stored.metrics == trial.metrics
    assert stored.metrics.measured_at > 0
    settings = await tuned_settings(tuning.service.index, tuning.service.settings)
    assert settings.graph.stopwords == []


async def test_a_trial_measures_the_active_set_plus_the_token_it_is_asked_about(
    tuning: TuningService, monkeypatch
):
    """Once one token is active, a trial that shaped `[new]` alone would score a graph nobody
    will ever build, against a baseline built with the active token (E2)."""
    first = await tuning.propose("stopwords", "loader", "one", now=1.0)
    await tuning.measure(first.tuning_id)
    await tuning.ledger.accept(str(first.tuning_id), token=first.token, cap=20)
    second = await tuning.propose("stopwords", "widget", "two", now=200_000.0)
    seen: list[list[str]] = []
    real = GraphBuilder.shape

    async def spy(self, index, settings, *, progress=None):
        seen.append(list(settings.graph.stopwords))
        return await real(self, index, settings, progress=progress)

    monkeypatch.setattr(GraphBuilder, "shape", spy)
    await tuning.measure(second.tuning_id)
    assert seen == [["loader", "widget"]]


async def test_a_trial_runs_off_the_loop_thread(tuning: TuningService, monkeypatch):
    """Spec 11 puts the trial on a worker thread, and this is what says it is on one (E5)."""
    caller = threading.get_ident()
    seen: list[int] = []
    real = GraphBuilder.shape

    async def spy(self, index, settings, *, progress=None):
        seen.append(threading.get_ident())
        return await real(self, index, settings, progress=progress)

    monkeypatch.setattr(GraphBuilder, "shape", spy)
    row = await tuning.propose("stopwords", "load", "one")
    await tuning.measure(row.tuning_id)
    assert seen and caller not in seen


async def test_one_repos_trial_does_not_stall_another_repos_loop(
    tuning: TuningService, refine_service: RefinementService, monkeypatch
):
    """Two repos, one event loop: the daemon runs every driver on `observer-loops`, so a trial
    that held that thread would freeze the other repo's ladder and `reconcile`'s 60 s
    timeout with it (E5)."""
    started, release = threading.Event(), threading.Event()
    real = GraphBuilder.shape

    async def blocking(self, index, settings, *, progress=None):
        started.set()
        release.wait(10.0)
        return await real(self, index, settings, progress=progress)

    monkeypatch.setattr(GraphBuilder, "shape", blocking)
    row = await tuning.propose("stopwords", "load", "one")
    other = TuningService(service=_service_with(refine_service, {}))

    async def second_repo() -> int:
        await asyncio.to_thread(started.wait, 10.0)
        rows = await other.ledger.rows()
        release.set()
        return len(rows)

    async with asyncio.timeout(30):
        trial, seen = await asyncio.gather(tuning.measure(row.tuning_id), second_repo())
    assert trial.metrics.measured_at > 0
    assert seen == 1


async def test_a_guard_that_refuses_lands_the_row_rejected_and_accept_says_so(
    tuning: TuningService, monkeypatch
):
    """The four guards are the point of the trial, so a trial that fails one has to be visible
    and unacceptable rather than a number nobody reads (E1)."""
    row = await tuning.propose("stopwords", "load", "one")
    real = GraphBuilder.shape

    async def fewer(self, index, settings, *, progress=None):
        write = await real(self, index, settings, progress=progress)
        return write.model_copy(update={"clusters": ()})

    monkeypatch.setattr(GraphBuilder, "shape", fewer)
    trial = await tuning.measure(row.tuning_id)
    assert not trial.passed
    assert "outside the 20% band" in trial.refused
    stored = await tuning.ledger.row(str(row.tuning_id))
    assert stored.status is TuningStatus.REJECTED
    assert stored.metrics.refused == trial.refused
    with pytest.raises(TuningRefused, match="failed a spec 11 guard"):
        await tuning.ledger.accept(str(row.tuning_id), token=row.token, cap=20)


async def test_a_refused_row_is_not_measured_again_by_the_loop(
    tuning: TuningService, monkeypatch
):
    """`rejected` is what stops the ladder paying for the same refusal every pass."""
    row = await tuning.propose("stopwords", "load", "one")
    real = GraphBuilder.shape

    async def fewer(self, index, settings, *, progress=None):
        write = await real(self, index, settings, progress=progress)
        return write.model_copy(update={"clusters": ()})

    monkeypatch.setattr(GraphBuilder, "shape", fewer)
    await tuning.measure(row.tuning_id)
    assert await tuning.unmeasured() is None


@pytest.mark.parametrize(
    "status",
    [TuningStatus.ACTIVE, TuningStatus.REVERTED, TuningStatus.SUPERSEDED],
    ids=["active", "reverted", "superseded"],
)
async def test_a_settled_row_cannot_be_measured_again(
    tuning: TuningService, status: TuningStatus
):
    """Invariant 3 keeps a reverted row's reason and its metrics, so a second trial may not
    overwrite them (M4)."""
    row = await tuning.propose("stopwords", "load", "one")
    await tuning.service.index.tuning.set_tuning_status(row.tuning_id, status)
    with pytest.raises(TuningRefused, match="can be measured"):
        await tuning.measure(row.tuning_id)


async def test_a_checkout_with_no_graph_refuses_once_and_is_not_retried(
    graph_store, tmp_path
):
    """An unbuilt checkout has no baseline, so every number would be zero and the loop would pay
    for a rebuild every pass forever (E4)."""
    service = RefinementService(
        graph_store, tmp_path, AuditorSettings(), UserSettings(), registry=RunRegistry()
    )
    tuning = TuningService(service=service)
    row = await tuning.propose("stopwords", "load", "one")
    trial = await tuning.measure(row.tuning_id)
    assert trial.refused == NO_GRAPH
    stored = await tuning.ledger.row(str(row.tuning_id))
    assert stored.status is TuningStatus.REJECTED
    assert stored.metrics.measured_at > 0
    assert await tuning.unmeasured() is None
    with pytest.raises(TuningRefused, match="no built graph"):
        await tuning.ledger.accept(str(row.tuning_id), token=row.token, cap=20)


async def test_the_loop_picks_the_oldest_unmeasured_proposal(tuning: TuningService):
    first = await tuning.propose("stopwords", "load", "one", now=1.0)
    second = await tuning.propose("stopwords", "widget", "two", now=200_000.0)
    waiting = await tuning.unmeasured()
    assert waiting is not None and waiting.tuning_id == first.tuning_id
    await tuning.measure(first.tuning_id)
    waiting = await tuning.unmeasured()
    assert waiting is not None and waiting.tuning_id == second.tuning_id
    await tuning.measure(second.tuning_id)
    assert await tuning.unmeasured() is None


async def _call(repo: Path, **kw: object) -> dict:
    """One `propose_tuning` call through the registered server, as an agent makes it."""
    async with Client(mcp) as client:
        return tool_data(
            await client.call_tool("propose_tuning", {"path": str(repo), **kw})
        )


async def test_the_mcp_tool_records_a_row_and_names_the_confirmation_word(
    refine_repo: Path,
):
    """Spec 9.5's in-session producer, for the one proposal kind that is not a refinement."""
    data = await _call(
        refine_repo, key="stopwords", value="helper", reason="every module says helper"
    )
    assert (data["key"], data["status"]) == ("stopwords", "pending")
    assert data["value"] == '"helper"'
    assert len(data["token"]) == TOKEN_LENGTH
    assert data["allow_list"] == ["stopwords"]


@pytest.mark.parametrize(
    ("args", "match"),
    [
        ({"key": "knn_k", "value": "9", "reason": "sharper"}, "not shipped"),
        ({"key": "detect", "value": "x", "reason": "why"}, "not tunable"),
        (
            {"key": "stopwords", "value": "Two Words", "reason": "why"},
            "not a stopword token",
        ),
        ({"key": "stopwords", "value": "helper", "reason": ""}, "needs a reason"),
    ],
    ids=["deferred", "unknown", "not-a-token", "no-reason"],
)
async def test_the_tool_refuses_by_name_and_writes_nothing(
    refine_repo: Path, args: dict, match: str
):
    """Spec 9.2's verifier for `propose_tuning` is "allow-list + range" and nothing else."""
    with pytest.raises(ToolError, match=match):
        await _call(refine_repo, **args)
    async with await open_index(refine_repo) as index:
        assert await TuningLedger(index=index).rows() == []


async def test_the_tool_is_annotated_as_a_writer():
    """A client decides whether to prompt from this: `propose_tuning` writes a row and opens a run."""
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "propose_tuning" in tools
    assert tools["propose_tuning"].annotations.readOnlyHint is not True
