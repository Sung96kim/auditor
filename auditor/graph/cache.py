"""The read-side index over one repo partition, shared by the flow walk and ``GraphQuery``.

``GraphCache`` is loaded once per query. It reads the whole partition, so it pays for itself from
roughly six visited nodes up, which ``build_flow`` passes immediately and ``neighbors`` reaches
from depth 2; a single-node read stays cheaper on one ``GraphDB.edges_of`` round trip.
"""

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auditor.database import IndexStore

NodeRow = dict[str, Any]  # one graph_nodes row, as GraphDB.nodes() returns it
EdgeRow = dict[str, Any]  # one graph_edges row: src, dst, kind, weight, provenance
QueueRow = dict[
    str, Any
]  # one graph_unresolved payload row, as GraphDB.unresolved() returns it


def resolve_ids(node_ids: Collection[str], symbol: str) -> list[str]:
    """Node ids matching ``symbol``: the exact id, else every ``::name`` / ``.name`` suffix.

    Shared by ``GraphQuery._resolve_all`` and ``GraphQuery.flow`` so a bare name resolves the same
    way whether the ids come from the database or from a loaded cache.
    """
    if symbol in node_ids:
        return [symbol]
    return sorted(
        nid
        for nid in node_ids
        if nid.endswith(f"::{symbol}") or nid.endswith(f".{symbol}")
    )


class GraphCache:
    """Every node and edge of one repo partition, indexed by ``src`` and by ``dst``."""

    def __init__(self, nodes: list[NodeRow], edges: list[EdgeRow]) -> None:
        self.nodes: dict[str, NodeRow] = {n["node_id"]: n for n in nodes}
        self.out: dict[str, list[EdgeRow]] = {}
        self.inc: dict[str, list[EdgeRow]] = {}
        for e in edges:
            self.out.setdefault(e["src"], []).append(e)
            self.inc.setdefault(e["dst"], []).append(e)

    @classmethod
    async def load(cls, index: "IndexStore") -> "GraphCache":
        return cls(await index.graph.nodes(), await index.graph.all_edges())

    def kind(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        return node["kind"] if node else "?"

    def module(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        return node["module"] if node else node_id.split("::")[0]

    def role(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        return node["role"] if node else "production"

    def rank(self, node_id: str) -> float:
        node = self.nodes.get(node_id)
        return (node.get("rank") or 0.0) if node else 0.0

    def outgoing(self, node_id: str, kinds: frozenset[str]) -> list[EdgeRow]:
        return [e for e in self.out.get(node_id, ()) if e["kind"] in kinds]

    def incoming(self, node_id: str, kinds: frozenset[str]) -> list[EdgeRow]:
        return [e for e in self.inc.get(node_id, ()) if e["kind"] in kinds]

    def incident(self, node_id: str, kinds: frozenset[str]) -> list[EdgeRow]:
        """Both directions, each edge once: a self-loop is in ``out`` and ``inc`` alike."""
        return self.outgoing(node_id, kinds) + [
            e for e in self.incoming(node_id, kinds) if e["src"] != node_id
        ]
