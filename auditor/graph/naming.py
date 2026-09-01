"""Naming-similarity edges via tf-idf + LSI (spec §9a). Needs numpy + scikit-learn."""

import numpy as np
from pydantic import BaseModel, ConfigDict
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.preprocessing import normalize

from auditor.graph.model import EdgeKind, GraphEdge, GraphNode
from auditor.graph.stemming import STEM
from auditor.graph.textmodel import TextModel
from auditor.graph.tokens import TEXT_FLOOR


class NamingPass(BaseModel):
    """One naming-similarity pass: the edges it drew, the symbols carrying too little text to
    cluster on, and the fit both came from, kept so a query can rank against it (spec §22)."""

    model_config = ConfigDict(frozen=True)

    edges: tuple[GraphEdge, ...] = ()
    sparse: frozenset[str] = frozenset()
    text_model: TextModel | None = None


def _text_model(
    node_ids: tuple[str, ...],
    vec: TfidfVectorizer,
    svd: TruncatedSVD,
    reduced: np.ndarray,
) -> TextModel:
    """Freeze the fit into blobs a query reads back: idf folded into the components, unit rows."""
    projection = np.ascontiguousarray(
        svd.components_.T * vec.idf_[:, None], dtype=np.float32
    )
    return TextModel(
        node_ids=node_ids,
        vocabulary={t: int(i) for t, i in vec.vocabulary_.items()},
        components=int(svd.components_.shape[0]),
        projection=projection.tobytes(),
        doc_vectors=np.ascontiguousarray(reduced, dtype=np.float32).tobytes(),
    )


def name_similar_edges(
    nodes: list[GraphNode],
    *,
    threshold: float = 0.45,
    knn_k: int = 8,
    extra_stopwords: tuple[str, ...] = (),
) -> NamingPass:
    sparse = frozenset(n.id for n in nodes if len(set(n.doc_tokens)) < TEXT_FLOOR)
    dense = [n for n in nodes if n.id not in sparse]
    if len(dense) < 2:
        return NamingPass(sparse=sparse)

    # english + repo-configured stopwords are filtered here (before stemming) — cleaner than
    # TfidfVectorizer(stop_words=...) which would mismatch the stemmed tokens. Structural stopwords
    # were already removed upstream in tokens.py; the domain-noun decision is config-driven (§17 POC).
    stop = ENGLISH_STOP_WORDS | set(extra_stopwords)
    docs = [" ".join(STEM(t) for t in n.doc_tokens if t not in stop) for n in dense]
    vec = TfidfVectorizer(token_pattern=r"(?u)\b\w\w+\b", min_df=1)
    try:
        x = vec.fit_transform(docs)
    except ValueError:  # empty vocabulary
        return NamingPass(sparse=sparse)
    n_comp = min(150, x.shape[0] - 1, x.shape[1] - 1)
    if n_comp < 2:
        return NamingPass(sparse=sparse)
    svd = TruncatedSVD(n_components=n_comp, random_state=0)
    reduced = normalize(svd.fit_transform(x))
    sim = reduced @ reduced.T

    edges: list[GraphEdge] = []
    seen: set[tuple[str, str]] = set()
    for i, node in enumerate(dense):
        order = np.argsort(-sim[i])
        kept = 0
        for j in order:
            if j == i:
                continue
            score = float(sim[i][j])
            if score < threshold or kept >= knn_k:
                break
            kept += 1
            a, b = sorted((node.id, dense[j].id))
            if (a, b) not in seen:
                seen.add((a, b))
                edges.append(
                    GraphEdge(
                        src=a, dst=b, kind=EdgeKind.NAME_SIMILAR, weight=round(score, 6)
                    )
                )
    edges.sort(key=lambda e: (e.src, e.dst))
    return NamingPass(
        edges=tuple(edges),
        sparse=sparse,
        text_model=_text_model(tuple(n.id for n in dense), vec, svd, reduced),
    )
