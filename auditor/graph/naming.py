"""Naming-similarity edges via tf-idf + LSI (spec §9a). Needs numpy + scikit-learn."""

import numpy as np
import snowballstemmer
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.preprocessing import normalize

from auditor.graph.model import EdgeKind, GraphEdge, GraphNode
from auditor.graph.tokens import TEXT_FLOOR

# Snowball (Porter2) stemmer — applied here in the IR layer (not in stdlib tokens.py) so morphological
# variants (reviewer/reviews/reviewing → review) share a term before tf-idf. No verb-synonym map:
# a POC measured it worthless; LSI discovers verb synonymy from co-occurrence.
_STEM = snowballstemmer.stemmer("english").stemWord


def name_similar_edges(
    nodes: list[GraphNode],
    *,
    threshold: float = 0.45,
    knn_k: int = 8,
    extra_stopwords: tuple[str, ...] = (),
) -> tuple[list[GraphEdge], set[str]]:
    sparse = {n.id for n in nodes if len(set(n.doc_tokens)) < TEXT_FLOOR}
    dense = [n for n in nodes if n.id not in sparse]
    if len(dense) < 2:
        return [], sparse

    # english + repo-configured stopwords are filtered here (before stemming) — cleaner than
    # TfidfVectorizer(stop_words=...) which would mismatch the stemmed tokens. Structural stopwords
    # were already removed upstream in tokens.py; the domain-noun decision is config-driven (§17 POC).
    stop = ENGLISH_STOP_WORDS | set(extra_stopwords)
    docs = [" ".join(_STEM(t) for t in n.doc_tokens if t not in stop) for n in dense]
    vec = TfidfVectorizer(token_pattern=r"(?u)\b\w\w+\b", min_df=1)
    try:
        x = vec.fit_transform(docs)
    except ValueError:  # empty vocabulary
        return [], sparse
    n_comp = min(150, x.shape[0] - 1, x.shape[1] - 1)
    if n_comp < 2:
        return [], sparse
    reduced = normalize(
        TruncatedSVD(n_components=n_comp, random_state=0).fit_transform(x)
    )
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
    return edges, sparse
