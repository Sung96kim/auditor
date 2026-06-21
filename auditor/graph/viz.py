"""Visualization data contract: build the graph payload the UI consumes.

Stdlib only — pure mapping over the persisted graph (auditor/graph/ui/ renders it).
"""

from auditor.graph.model import NodeKind

_TYPE = {
    NodeKind.CLASS: "class",
    NodeKind.FUNCTION: "function",
    NodeKind.METHOD: "method",
    NodeKind.MODULE: "module",
}


def _node_type(kind: str) -> str:
    if kind in NodeKind._value2member_map_:
        return _TYPE.get(NodeKind(kind), "function")
    return "function"


def _agg_rank(raw_nodes: list[dict], cid: int | None) -> float:
    return sum(n["rank"] for n in raw_nodes if n["cluster_id"] == cid)


async def _findings_by_node(index) -> dict[str, list[str]]:
    """Map node_id -> [graph rule_ids]. Graph findings store the symbol id in ``evidence``."""
    out: dict[str, list[str]] = {}
    for f in await index.findings.by_rule_prefix("GRAPH-"):
        out.setdefault(f["evidence"], []).append(f["rule_id"])
    return out


async def build_payload(index, *, node_cap: int = 200) -> dict:
    """Return the graph payload consumed by the visualization UI.

    Shape: ``{meta, clusters, nodes, edges}`` — see §4 of the Phase V contract.
    Output is deterministic: nodes sorted by id, edges by (src, dst, kind),
    clusters by cluster_id.
    """
    raw_nodes = sorted(await index.graph.nodes(), key=lambda n: n["node_id"])
    findings_by_node = await _findings_by_node(index)

    nodes = []
    for n in raw_nodes[:node_cap]:
        nid = n["node_id"]
        nodes.append(
            {
                "id": nid,
                "label": nid.split("::")[-1] if "::" in nid else nid,
                "type": _node_type(n["kind"]),
                "lang": "python",
                "module": n["module"],
                "path": n["module"],
                "line": n["line"],
                "rank": round(n["rank"], 6),
                "cluster": n["cluster_id"],
                "role": n["role"],
                "findings": findings_by_node.get(nid, []),
            }
        )

    keep = {n["id"] for n in nodes}
    edges = []
    for e in sorted(
        await index.graph.all_edges(), key=lambda e: (e["src"], e["dst"], e["kind"])
    ):
        if e["src"] in keep and e["dst"] in keep:
            edges.append(
                {
                    "source": e["src"],
                    "target": e["dst"],
                    "kind": e["kind"],
                    "weight": round(e["weight"], 4),
                }
            )

    clusters = [
        {
            "cluster_id": c["cluster_id"],
            "label": c["label"],
            "member_count": c["member_count"],
            "agg_rank": round(_agg_rank(raw_nodes, c["cluster_id"]), 6),
        }
        for c in sorted(await index.graph.clusters(), key=lambda c: c["cluster_id"])
    ]

    return {
        "meta": {"theme": "dark", "accent": "#7C7CFF", "node_cap": node_cap},
        "clusters": clusters,
        "nodes": nodes,
        "edges": edges,
    }
