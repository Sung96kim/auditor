"""Query-time ranking against the stored tf-idf + LSI fit (spec section 22). Needs numpy."""

import numpy as np

from auditor.graph.stemming import STEM
from auditor.graph.textmodel import TextModel
from auditor.graph.tokens import normalize_tokens, split_ident


def query_terms(term: str) -> list[str]:
    """The stemmed tokens ``term`` contributes, tokenized the way the documents were."""
    return [STEM(t) for t in normalize_tokens(split_ident(term))]


def score(model: TextModel, term: str) -> dict[str, float]:
    """Cosine of ``term`` against every document the build fitted, keyed by node id.

    Empty when the query carries no term the corpus was fitted on, which is the honest answer
    rather than a ranking over noise.
    """
    if not model.usable:
        return {}
    rows = [model.vocabulary[t] for t in query_terms(term) if t in model.vocabulary]
    if not rows:
        return {}
    projection = np.frombuffer(model.projection, dtype=np.float32).reshape(
        -1, model.components
    )
    query = projection[rows].sum(axis=0)
    norm = float(np.linalg.norm(query))
    if norm == 0.0:
        return {}
    documents = np.frombuffer(model.doc_vectors, dtype=np.float32).reshape(
        len(model.node_ids), model.components
    )
    return dict(zip(model.node_ids, (documents @ (query / norm)).tolist(), strict=True))
