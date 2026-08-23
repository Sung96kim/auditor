"""Per-node and per-file fact hashes (spec 5.5): what a refinement anchors on.

``truth_sha`` covers the facts structural edges are derived from; ``facts_sha`` adds ``doc_tokens``,
which similarity edges and ``text_sparse`` also read. Neither covers ``line``, ``role``, the
identity strings the id already carries, the build-pass fields, or ``local_names``.
"""

import hashlib
import json
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from auditor.graph.model import UNION_FACT_FIELDS, GraphNode

#: outside `truth_sha`: a docstring moves no structural edge, and neither does a renamed local.
NON_TRUTH_FACT_FIELDS = frozenset({"doc_tokens", "local_names"})
#: outside `facts_sha` too, so spec 8.6 stage 1 skips a rename with no rebuild: `local_names` only
#: gates queue rows, and every build rebuilds the whole queue.
NON_FACTS_FACT_FIELDS = frozenset({"local_names"})

TRUTH_FACT_FIELDS = tuple(
    f for f in UNION_FACT_FIELDS if f not in NON_TRUTH_FACT_FIELDS
)
FACTS_FACT_FIELDS = tuple(
    f for f in UNION_FACT_FIELDS if f not in NON_FACTS_FACT_FIELDS
)


class FileHashes(BaseModel):
    """One file's two rolled hashes, as stored beside its cached facts."""

    model_config = ConfigDict(frozen=True)

    truth: str
    facts: str


def _digest(node: GraphNode, fields: tuple[str, ...]) -> str:
    payload = {
        "kind": node.kind.value,
        "is_hof": node.is_hof,
        "is_stub": node.is_stub,
        **{field: getattr(node, field) for field in fields},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def node_truth_sha(node: GraphNode) -> str:
    return _digest(node, TRUTH_FACT_FIELDS)


def node_facts_sha(node: GraphNode) -> str:
    return _digest(node, FACTS_FACT_FIELDS)


def _roll(nodes: Sequence[GraphNode], per_node: Callable[[GraphNode], str]) -> str:
    pairs = sorted(f"{n.id}:{per_node(n)}" for n in nodes)
    return hashlib.sha256("\n".join(pairs).encode()).hexdigest()


def file_hashes(nodes: Sequence[GraphNode]) -> FileHashes:
    """Both hashes for one file, rolled over the sorted ``(node_id, hash)`` set — so an added or
    deleted node moves the file hash even when every surviving node is unchanged."""
    return FileHashes(
        truth=_roll(nodes, node_truth_sha), facts=_roll(nodes, node_facts_sha)
    )
