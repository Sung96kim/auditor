"""Spec 11's knob tuning: the allow-list, the per-token stopword model, the precedence read,
the trial's metrics and guards, and the proposal lifecycle."""

import json

import pytest

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
    TuningBaseline,
    TuningMetrics,
    TuningRow,
    TuningStatus,
)
from auditor.graph.refine.trial import _guard, _stranded_pins, baseline_of, measured
from auditor.graph.refine.tuning import (
    TOKEN_LENGTH,
    TUNING_KNOBS,
    TuningRefused,
    active_stopwords,
    candidate_stopwords,
    confirmation_token,
    knob,
    stopword,
    tuned,
)


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
