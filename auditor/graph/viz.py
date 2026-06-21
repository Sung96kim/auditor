"""Visualization data contract: build the graph payload the UI consumes.

Stdlib only — pure mapping over the persisted graph (auditor/graph/ui/ renders it).
"""

import json
from pathlib import Path

from auditor.graph.model import NodeKind

_APP_HTML = Path(__file__).parent / "ui" / "dist" / "index.html"

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


def render_app(payload: dict) -> str:
    """Inject ``payload`` into the built UI HTML and return the result.

    The global ``window.__AUDITOR_GRAPH__`` is injected immediately before
    ``</body>`` so the app bundle can read it at startup.
    """
    if not _APP_HTML.exists():
        raise FileNotFoundError(
            f"Built UI not found at {_APP_HTML}. "
            "Run `pnpm build` inside auditor/graph/ui/ first."
        )
    html = _APP_HTML.read_text(encoding="utf-8")
    blob = json.dumps(payload).replace("</", "<\\/")  # avoid </script> breakage
    inject = f"<script>window.__AUDITOR_GRAPH__={blob};</script>"
    if "</body>" in html:
        return html.replace("</body>", inject + "</body>", 1)
    return html + inject


def to_dot(
    payload: dict,
    *,
    cluster: str | None = None,
    symbol: str | None = None,
    depth: int = 1,
) -> str:
    """Return a deterministic Graphviz DOT string for the payload.

    Default: overview (all kept nodes).
    ``cluster``: members of the cluster with that label.
    ``symbol``: BFS ego graph from matching node(s) to ``depth``.
    """
    nodes = {n["id"]: n for n in payload["nodes"]}
    edges = payload["edges"]
    keep: set[str]
    if symbol is not None:
        seeds = {
            nid
            for nid in nodes
            if nid.endswith(f"::{symbol}")
            or nid.endswith(f".{symbol}")
            or nid == symbol
        }
        keep = set(seeds)
        frontier = set(seeds)
        for _ in range(depth):
            nxt = set()
            for e in edges:
                if e["source"] in frontier and e["target"] not in keep:
                    nxt.add(e["target"])
                if e["target"] in frontier and e["source"] not in keep:
                    nxt.add(e["source"])
            keep |= nxt
            frontier = nxt
    elif cluster is not None:
        cid = next(
            (c["cluster_id"] for c in payload["clusters"] if c["label"] == cluster),
            None,
        )
        keep = {nid for nid, n in nodes.items() if n["cluster"] == cid}
    else:
        keep = set(nodes)
    lines = [
        "digraph codebase {",
        "  rankdir=LR;",
        "  node [shape=box, style=rounded];",
    ]
    for nid in sorted(keep):
        lines.append(f'  "{nid}" [label="{nodes[nid]["label"]}"];')
    for e in sorted(
        (e for e in edges if e["source"] in keep and e["target"] in keep),
        key=lambda e: (e["source"], e["target"], e["kind"]),
    ):
        lines.append(f'  "{e["source"]}" -> "{e["target"]}" [label="{e["kind"]}"];')
    lines.append("}")
    return "\n".join(lines)
