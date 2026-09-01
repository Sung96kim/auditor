"""Query-time ranking against the stored tf-idf + LSI fit (spec section 22). Needs numpy."""

import numpy as np

from auditor.graph.stemming import STEM
from auditor.graph.textmodel import TEXT_MODEL_DTYPE, TextModel
from auditor.graph.tokens import normalize_tokens, split_ident

#: below this cosine a document is noise rather than a candidate, so the caller drops it. Measured
#: on this repo's own fit: about half of all 6496 documents score at or below zero for any query,
#: while the weakest page the retrieval gate recovers an answer from bottoms out at 0.32, so the
#: floor sits an order of magnitude under the weakest real answer and above the noise band.
RELEVANCE_FLOOR = 0.05


def query_terms(term: str) -> list[str]:
    """The stemmed tokens ``term`` contributes, tokenized the way the documents were."""
    # no stop list here: the documents were filtered before stemming, so the vocabulary lookup
    # below is the filter. A repo that configures `graph.stopwords` gets the asymmetry S11 owns.
    return [STEM(t) for t in normalize_tokens(split_ident(term))]


def score(model: TextModel, term: str) -> dict[str, float]:
    """Cosine of ``term`` against every document the build fitted, keyed by node id.

    Empty when the query carries no term the corpus was fitted on, which is the honest answer
    rather than a ranking over noise. Signed: the caller applies :data:`RELEVANCE_FLOOR`.
    """
    if not model.usable:
        return {}
    rows = [model.vocabulary[t] for t in query_terms(term) if t in model.vocabulary]
    if not rows:
        return {}
    projection = np.frombuffer(model.projection, dtype=TEXT_MODEL_DTYPE).reshape(
        -1, model.components
    )
    query = projection[rows].sum(axis=0)
    norm = float(np.linalg.norm(query))
    if norm == 0.0:
        return {}
    # the whole fit is materialised and scored per query by construction; a partial read would
    # need a top-k index on the stored blob, which is the change a much larger corpus would force
    documents = np.frombuffer(model.doc_vectors, dtype=TEXT_MODEL_DTYPE).reshape(
        len(model.node_ids), model.components
    )
    return dict(zip(model.node_ids, (documents @ (query / norm)).tolist(), strict=True))
