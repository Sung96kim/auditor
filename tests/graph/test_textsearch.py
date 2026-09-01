"""The stored tf-idf + LSI fit: what the build keeps, what a query reads, and the two tiers."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auditor.cli import app
from auditor.database import IndexStore
from auditor.database.graph import GraphDB
from auditor.graph.model import GraphNode, NodeKind
from auditor.graph.naming import name_similar_edges
from auditor.graph.query import GraphQuery
from auditor.graph.textmodel import TEXT_MODEL_KIND, TextModel
from auditor.graph.textsearch import query_terms, score

runner = CliRunner()

WORDS = {
    "reader": ["read", "user", "account", "profile", "record"],
    "writer": ["write", "invoice", "payment", "charge", "refund"],
    "mixer": ["read", "invoice", "user", "charge", "profile"],
}


def _node(name: str, tokens: list[str]) -> GraphNode:
    return GraphNode(
        id=f"m.py::{name}",
        kind=NodeKind.FUNCTION,
        name=name,
        module="m.py",
        qualname=name,
        doc_tokens=tuple(tokens),
    )


@pytest.fixture
def fitted() -> TextModel:
    """The fit over three documents: two opposed vocabularies and one that straddles them."""
    model = name_similar_edges([_node(n, t) for n, t in WORDS.items()]).text_model
    assert model is not None
    return model


def test_the_fit_is_kept_and_covers_every_dense_document(fitted: TextModel):
    assert fitted.usable
    assert fitted.node_ids == ("m.py::reader", "m.py::writer", "m.py::mixer")
    assert len(fitted.doc_vectors) == 4 * len(fitted.node_ids) * fitted.components
    assert len(fitted.projection) == 4 * len(fitted.vocabulary) * fitted.components


def test_the_fit_is_deterministic():
    """Two fits over one corpus must agree byte for byte, or a rebuild would reorder search."""
    nodes = [_node(n, t) for n, t in WORDS.items()]
    assert name_similar_edges(nodes).text_model == name_similar_edges(nodes).text_model


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


def _search(repo: Path, term: str) -> list[dict]:
    result = runner.invoke(app, ["graph", "search", term, str(repo), "--json"])
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


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


async def test_search_without_a_stored_fit_is_the_substring_scan(
    query_store: IndexStore,
):
    """An index built before this slice has no fit, and must still answer by name."""
    assert await query_store.graph.text_model() is None
    rows = (await GraphQuery(query_store).search("user")).root
    assert [r.id for r in rows] == ["m.py::get_user", "m.py::fetch_user"]
    assert all(r.score == 0.0 for r in rows)


def test_a_built_repo_answers_a_question_no_id_contains(built_text_repo: Path):
    """End to end: no id holds these words, so only the stored fit can answer, with a score."""
    rows = _search(built_text_repo, "an outstanding balance")
    assert [r["id"] for r in rows[:2]] == [
        "m.py::charge_invoice_payment",
        "m.py::refund_invoice_payment",
    ]
    assert all(r["score"] > 0.0 for r in rows[:2])


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
