"""Spec 11's knob tuning: the allow-list, the per-token stopword model, the precedence read,
the trial's metrics and guards, and the proposal lifecycle."""

import json

import pytest

from auditor.config import AuditorSettings, GraphConfig, load_config
from auditor.graph.build import tuned_settings
from auditor.graph.refine.models import Run, TuningRow, TuningStatus
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
