"""The stored tf-idf + LSI fit: what the build keeps, what a query reads, and the two tiers."""

import json
import logging
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from auditor.cli import app
from auditor.database import IndexStore
from auditor.database.graph import GraphDB
from auditor.graph.model import MAX_SEARCH_LIMIT, GraphNode, NodeKind, search_limit
from auditor.graph.naming import name_similar_edges
from auditor.graph.query import GraphQuery
from auditor.graph.textmodel import (
    TEXT_MODEL_DTYPE,
    TEXT_MODEL_ITEMSIZE,
    TEXT_MODEL_KIND,
    TextModel,
)
from auditor.graph.textsearch import RELEVANCE_FLOOR, query_terms, score

runner = CliRunner()

WORDS = {
    "reader": ["read", "user", "account", "profile", "record"],
    "writer": ["write", "invoice", "payment", "charge", "refund"],
    "mixer": ["read", "invoice", "user", "charge", "profile"],
}
#: under TEXT_FLOOR unique tokens, so the fit must leave it out of both halves of the model
SPARSE_WORDS = {"stub": ["read"]}


def _node(name: str, tokens: list[str]) -> GraphNode:
    return GraphNode(
        id=f"m.py::{name}",
        kind=NodeKind.FUNCTION,
        name=name,
        module="m.py",
        qualname=name,
        doc_tokens=tuple(tokens),
    )


def _corpus() -> list[GraphNode]:
    """Three documents dense enough to fit, plus one the text floor excludes."""
    return [_node(n, t) for n, t in (*WORDS.items(), *SPARSE_WORDS.items())]


@pytest.fixture
def fitted() -> TextModel:
    """The fit over three documents: two opposed vocabularies and one that straddles them."""
    model = name_similar_edges(_corpus()).text_model
    assert model is not None
    return model


def test_the_fit_is_kept_and_covers_every_dense_document(fitted: TextModel):
    assert fitted.usable
    assert fitted.node_ids == ("m.py::reader", "m.py::writer", "m.py::mixer")
    width = TEXT_MODEL_ITEMSIZE * fitted.components
    assert len(fitted.doc_vectors) == width * len(fitted.node_ids)
    assert len(fitted.projection) == width * len(fitted.vocabulary)


def test_the_fit_is_deterministic():
    """Two fits over one corpus must agree byte for byte, or a rebuild would reorder search."""
    assert (
        name_similar_edges(_corpus()).text_model
        == name_similar_edges(_corpus()).text_model
    )


def test_the_fit_covers_the_dense_documents_and_only_those():
    """A document the floor excluded has no row in ``doc_vectors``, so it must have no id either.

    Fitting every node instead would leave ``node_ids`` longer than the blob and raise on the
    reshape at query time, which is the shape this pins.
    """
    naming = name_similar_edges(_corpus())
    assert naming.sparse == {"m.py::stub"}
    assert naming.text_model is not None
    assert "m.py::stub" not in naming.text_model.node_ids


def test_the_projection_carries_the_idf_weights(fitted: TextModel):
    """Without the fold a rare term and a common one would pull a query equally."""
    projection = np.frombuffer(fitted.projection, dtype=TEXT_MODEL_DTYPE).reshape(
        -1, fitted.components
    )
    norm = {
        t: float(np.linalg.norm(projection[i])) for t, i in fitted.vocabulary.items()
    }
    # "read" is in two of the three documents, "refund" in one, so idf must separate them
    assert norm["read"] < norm["refund"]


async def test_the_model_round_trips_through_the_index(
    graph_store: IndexStore, fitted: TextModel
):
    await graph_store.transaction(
        lambda conn: graph_store.graph.write_text_model(conn, fitted)
    )
    assert await graph_store.graph.text_model() == fitted


async def test_a_build_that_fits_nothing_clears_the_stored_fit(
    graph_store: IndexStore, fitted: TextModel
):
    """A stale fit would rank a query against node ids this build no longer has."""
    await graph_store.transaction(
        lambda conn: graph_store.graph.write_text_model(conn, fitted)
    )
    await graph_store.transaction(
        lambda conn: graph_store.graph.write_text_model(conn, None)
    )
    assert await graph_store.graph.text_model() is None


def test_the_registry_owns_the_table_as_a_cache():
    """It is derived from the graph, so a schema bump must drop it with everything else."""
    assert GraphDB.TABLES["graph_text_model"].cache is True
    assert TEXT_MODEL_KIND == "tfidf_lsi"


@pytest.fixture
def built_text_repo(graph_repo_text: Path) -> Path:
    """``graph_repo_text`` scanned and built, so its fit is on disk for the CLI to rank with."""
    assert runner.invoke(app, ["scan", str(graph_repo_text), "-i"]).exit_code == 0
    assert runner.invoke(app, ["graph", "build", str(graph_repo_text)]).exit_code == 0
    return graph_repo_text


def _search(repo: Path, term: str, *flags: str) -> list[dict]:
    result = runner.invoke(app, ["graph", "search", term, str(repo), "--json", *flags])
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


#: documents for the three ids ``query_store`` already holds, so a ranked page is not empty
STORED_WORDS = {
    "get_user": ["load", "stored", "account", "profile", "record"],
    "fetch_user": ["fetch", "saved", "profile", "record", "account"],
    "charge": ["charge", "invoice", "payment", "outstanding", "balance"],
}
#: every way a stored row can disagree with itself, as a torn cache row would
TORN = {
    "projection short a byte": "SET projection = substr(projection, 1, length(projection) - 1)",
    "projection short a value": f"SET projection = substr(projection, 1, length(projection) - {TEXT_MODEL_ITEMSIZE})",
    "documents short a value": f"SET doc_vectors = substr(doc_vectors, 1, length(doc_vectors) - {TEXT_MODEL_ITEMSIZE})",
    "components off by one": "SET components = components + 1",
    "both blobs emptied": "SET projection = x'', doc_vectors = x''",
    "vocabulary not json": "SET vocabulary = 'not json at all'",
}


@pytest.fixture
async def ranked_store(query_store: IndexStore) -> IndexStore:
    """``query_store`` plus a fit over its own node ids, so the ranked tier can answer it."""
    model = name_similar_edges(
        [_node(n, t) for n, t in STORED_WORDS.items()]
    ).text_model
    await query_store.transaction(
        lambda conn: query_store.graph.write_text_model(conn, model)
    )
    return query_store


async def _tear(store: IndexStore, sql: str) -> None:
    def op(conn: sqlite3.Connection) -> None:
        conn.execute(f"UPDATE graph_text_model {sql}")  # noqa: S608  (test-local literal)

    await store.transaction(op)


@pytest.mark.parametrize(
    ("term", "winner"),
    [
        ("read the user account", "m.py::reader"),
        ("charge an invoice payment", "m.py::writer"),
    ],
)
def test_the_nearest_document_wins(fitted: TextModel, term: str, winner: str):
    scores = score(fitted, term)
    assert max(scores, key=lambda n: scores[n]) == winner


def test_a_query_with_no_known_term_scores_nothing(fitted: TextModel):
    """An unranked answer beats a ranking over noise, and the caller falls back to substrings."""
    assert score(fitted, "kubernetes helm chart") == {}


def test_a_query_is_stemmed_the_way_documents_were():
    assert query_terms("reviewing the submissions") == ["review", "submiss"]


def test_an_unfitted_model_scores_nothing():
    assert score(TextModel(), "read the user account") == {}


@pytest.mark.parametrize(
    "update",
    [
        {"node_ids": ()},
        {"vocabulary": {}},
        {"components": 0, "projection": b"", "doc_vectors": b""},
        {"projection": b""},
        {"doc_vectors": b""},
        {"components": 1},
    ],
    ids=[
        "no ids",
        "no vocabulary",
        "no components",
        "no projection",
        "no documents",
        "wrong width",
    ],
)
def test_a_fit_whose_parts_disagree_is_not_usable(
    fitted: TextModel, update: dict[str, object]
):
    """``usable`` is the guard between a stored row and a reshape, so it checks the blobs too."""
    assert not fitted.model_copy(update=update).usable


async def test_search_without_a_stored_fit_is_the_substring_scan(
    query_store: IndexStore,
):
    """An index built before this slice has no fit, and must still answer by name."""
    assert await query_store.graph.text_model() is None
    rows = (await GraphQuery(query_store).search("user")).root
    assert [r.id for r in rows] == ["m.py::get_user", "m.py::fetch_user"]
    assert all(r.score == 0.0 for r in rows)


def test_a_built_repo_answers_a_question_no_id_contains(built_text_repo: Path):
    """End to end: no id holds these words, so only the stored fit can answer, with a score.

    The whole page is asserted, not its head: the four unrelated symbols this repo holds scored
    zero or below before the relevance floor and reported ``score`` 0.0, which is the name half's
    signal.
    """
    rows = _search(built_text_repo, "an outstanding balance")
    assert [r["id"] for r in rows] == [
        "m.py::charge_invoice_payment",
        "m.py::refund_invoice_payment",
    ]
    assert all(r["score"] >= RELEVANCE_FLOOR for r in rows)


def test_a_word_only_the_docstrings_carry_is_ranked_rather_than_missed(
    built_text_repo: Path,
):
    """ "balance" is in the fitted vocabulary and in no id, which is the case the ranked tier is
    for: it answers with the two functions whose documents hold the word, not with nothing."""
    rows = _search(built_text_repo, "balance")
    assert [r["id"] for r in rows] == [
        "m.py::charge_invoice_payment",
        "m.py::refund_invoice_payment",
    ]


def test_the_name_half_honours_the_limit(built_text_repo: Path):
    """Two ids hold "user", so a limit of one must cut the page and keep the name tier."""
    rows = _search(built_text_repo, "user", "--limit", "1")
    assert [r["id"] for r in rows] == ["m.py::fetch_user_profile"]
    assert rows[0]["score"] == 0.0


def test_a_limit_below_one_is_a_usage_error_not_a_truncated_answer(
    built_text_repo: Path,
):
    """It used to accept -1, drop a real name match, and read as "only one symbol matches"."""
    result = runner.invoke(
        app, ["graph", "search", "user", str(built_text_repo), "--limit", "-1"]
    )
    assert result.exit_code == 2


@pytest.mark.parametrize("limit", [0, -1, MAX_SEARCH_LIMIT + 1])
def test_the_surfaces_without_an_argument_parser_reject_that_limit_too(limit: int):
    """The MCP tool has no typer to reject it, so the shared guard is what says no."""
    with pytest.raises(ValueError, match="limit must be 1 to"):
        search_limit(limit)


async def test_a_ranked_page_comes_back_when_the_fit_is_intact(
    ranked_store: IndexStore,
):
    """The control for the torn rows below: this query has no name match and a real answer."""
    rows = (await GraphQuery(ranked_store).search("an outstanding balance")).root
    assert [r.id for r in rows] == ["m.py::charge"]
    assert rows[0].score >= RELEVANCE_FLOOR


async def test_a_zero_limit_page_still_never_reads_the_fit(
    ranked_store: IndexStore, monkeypatch: pytest.MonkeyPatch
):
    """The tier gate reads the match set, not the truncated page, so an id that matches keeps
    the ranked half shut even when the cap leaves no row to show for it."""

    async def refuse() -> TextModel | None:
        raise AssertionError("the ranked tier ran for a term an id contains")

    monkeypatch.setattr(ranked_store.graph, "text_model", refuse)
    assert (await GraphQuery(ranked_store).search("user", limit=0)).root == ()


async def test_replacing_the_graph_drops_the_fit_that_described_the_old_one(
    ranked_store: IndexStore,
):
    """``replace`` swaps the nodes without a build, and a fit outliving them ranks against ids
    the graph no longer has."""
    await ranked_store.graph.replace([_node("only", ["x"])], [], [])
    assert await ranked_store.graph.text_model() is None


@pytest.mark.parametrize("sql", TORN.values(), ids=list(TORN))
async def test_a_torn_fit_answers_by_name_instead_of_raising(
    ranked_store: IndexStore, caplog: pytest.LogCaptureFixture, sql: str
):
    """The index is a cache: a row a bit-flip or a half-written blob broke must degrade to the
    substring answer and say so once, not hand the CLI a reshape traceback."""
    await _tear(ranked_store, sql)
    caplog.set_level(logging.WARNING, logger="auditor.database.graph")
    assert await ranked_store.graph.text_model() is None
    assert sum("malformed tfidf_lsi fit" in m for m in caplog.messages) == 1
    assert (await GraphQuery(ranked_store).search("an outstanding balance")).root == ()
    named = (await GraphQuery(ranked_store).search("user")).root
    assert [r.id for r in named] == ["m.py::get_user", "m.py::fetch_user"]


def test_a_name_match_returns_the_page_it_returned_before(built_text_repo: Path):
    """The ranked tier is the fallback, not a tail: a name lookup answers exactly as it did."""
    rows = _search(built_text_repo, "user")
    assert [r["id"] for r in rows] == [
        "m.py::fetch_user_profile",
        "m.py::read_user_account",
    ]
    assert all(r["score"] == 0.0 for r in rows)


def test_a_term_this_repo_has_no_word_for_still_returns_nothing(built_text_repo: Path):
    """An empty page says the repo has no such thing, and the ranked tier must not bury it."""
    assert _search(built_text_repo, "kubernetes") == []
