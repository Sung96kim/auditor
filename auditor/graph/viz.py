"""Visualization data contract: build the graph payload the UI consumes.

Pure mapping over the persisted graph (auditor/graph/ui/ renders it). Stdlib, plus the flow
models, so the DOT export reads the walk result rather than a dump of it.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING

from auditor.graph.flow import FlowNode, FlowPayload
from auditor.graph.model import NodeKind, Provenance

if TYPE_CHECKING:
    from auditor.database import IndexStore

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


async def _findings_by_node(index: "IndexStore") -> dict[str, list[str]]:
    """Map node_id -> [graph rule_ids]. Graph findings store the symbol id in ``evidence``."""
    out: dict[str, list[str]] = {}
    for f in await index.findings.by_rule_prefix("GRAPH-"):
        out.setdefault(f.evidence, []).append(f.rule_id)
    return out


async def build_payload(index: "IndexStore", *, node_cap: int | None = None) -> dict:
    """Return the graph payload consumed by the visualization UI.

    Shape: ``{meta, clusters, nodes, edges}`` — see §4 of the Phase V contract.
    Output is deterministic: nodes sorted by id, edges by (src, dst, kind),
    clusters by cluster_id.

    By default the FULL graph is included. The overview only renders clusters, but
    the cluster drill-down and node ego views need every node + edge to be complete —
    capping by top-rank starves them (a node's real neighbours are mostly lower-rank).
    ``node_cap`` keeps only the top-N nodes by rank as an optional safety valve for
    pathologically large graphs.
    """
    all_nodes = await index.graph.nodes()
    ranked = sorted(all_nodes, key=lambda n: (-n["rank"], n["node_id"]))
    top = ranked[:node_cap] if node_cap is not None else ranked
    raw_nodes = sorted(top, key=lambda n: n["node_id"])
    findings_by_node = await _findings_by_node(index)

    nodes = []
    for n in raw_nodes:
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
                "refined": bool(n["refined"]),
                "annotation": n["annotation"],
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
                    "provenance": e["provenance"],
                    "confirmed": bool(e["confirmed"]),
                }
            )

    clusters = [
        {
            "cluster_id": c["cluster_id"],
            "label": c["label"],
            "member_count": c["member_count"],
            "agg_rank": round(_agg_rank(all_nodes, c["cluster_id"]), 6),
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


def _dot_provenance(provenance: str | None) -> str:
    """The style a `refined` edge carries in both DOT exports, so an overlay edge cannot read as
    one the resolver produced. Empty for everything else."""
    return ' style="dashed"' if provenance == Provenance.REFINED.value else ""


_FLOW_DOT_STYLE = {
    "hub": ' color="magenta" peripheries=2',
    "stopped": ' color="cyan" style="rounded,dashed"',
    "cycle": ' color="orange"',
    "seen_ref": ' style="rounded,dotted"',
}


def _flow_declare(node: FlowNode, nodes: dict[str, dict]) -> str:
    """One DOT node line carrying the tree's ⊕/⊣/↺ marks and its unresolved count, so a pruned
    branch cannot read as an ordinary leaf."""
    label = nodes.get(node.id, {}).get("label") or node.id.split("::")[-1]
    if node.unresolved:
        label = f"{label}\\n? {len(node.unresolved)}"
    marks = "".join(a for mark, a in _FLOW_DOT_STYLE.items() if getattr(node, mark))
    return f'  "{node.id}" [label="{label}"{marks}];'


def _flow_dot(flow: FlowPayload, nodes: dict[str, dict]) -> str:
    """A flow tree as DOT: one ``rank=same`` row per depth, edges labelled by relation, nodes
    carrying the same marks the tree renderer shows."""
    declared: dict[str, str] = {}
    levels: dict[int, list[str]] = {}
    links: set[tuple[str, str, str, str]] = set()

    def walk(node: FlowNode) -> None:
        if node.id not in declared:  # a revisited node keeps its first-seen row
            declared[node.id] = _flow_declare(node, nodes)
            levels.setdefault(node.depth, []).append(node.id)
        for child in node.children:
            links.add((node.id, child.id, child.edge or "", child.source))
            walk(child)

    walk(flow.root)
    cut = ", truncated" if flow.truncated else ""
    lines = [
        "digraph flow {",
        f"  // {flow.direction.value}, at most {flow.limit} nodes{cut}",
        "  rankdir=LR;",
        "  node [shape=box, style=rounded];",
    ]
    lines.extend(declared[nid] for nid in sorted(declared))
    for level in sorted(levels):
        lines.append(
            "  { rank=same; " + " ".join(f'"{n}";' for n in levels[level]) + " }"
        )
    for src, dst, kind, provenance in sorted(links):
        lines.append(
            f'  "{src}" -> "{dst}" [label="{kind}"{_dot_provenance(provenance)}];'
        )
    lines.append("}")
    return "\n".join(lines)


def to_dot(
    payload: dict,
    *,
    cluster: str | None = None,
    symbol: str | None = None,
    depth: int = 1,
    flow: FlowPayload | None = None,
) -> str:
    """Return a deterministic Graphviz DOT string for the payload.

    Default: overview (all kept nodes).
    ``cluster``: members of the cluster with that label.
    ``symbol``: BFS ego graph from matching node(s) to ``depth``.
    ``flow``: a ``GraphQuery.flow`` payload, ranked one row per depth.
    """
    nodes = {n["id"]: n for n in payload["nodes"]}
    if flow is not None:
        return _flow_dot(flow, nodes)
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
        lines.append(
            f'  "{e["source"]}" -> "{e["target"]}" '
            f'[label="{e["kind"]}"{_dot_provenance(e.get("provenance"))}];'
        )
    lines.append("}")
    return "\n".join(lines)
