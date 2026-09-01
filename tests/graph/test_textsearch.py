"""The stored tf-idf + LSI fit: what the build keeps, what a query reads, and the two tiers."""

import pytest

from auditor.database import IndexStore
from auditor.database.graph import GraphDB
from auditor.graph.model import GraphNode, NodeKind
from auditor.graph.naming import name_similar_edges
from auditor.graph.textmodel import TEXT_MODEL_KIND, TextModel

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
