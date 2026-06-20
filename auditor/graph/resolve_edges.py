"""Resolve a node set's local facts into structural GraphEdges (spec §5). Stdlib only."""

from collections import defaultdict

from auditor.graph.model import EdgeKind, GraphEdge, GraphNode

_FN_KINDS = ("function", "method")


def resolve_structural(nodes: list[GraphNode]) -> list[GraphEdge]:
    fns = {n.id: n for n in nodes if n.kind in _FN_KINDS}
    classes = {n.id: n for n in nodes if n.kind == "class"}
    by_fn_name: dict[str, list[str]] = defaultdict(list)
    for n in fns.values():
        by_fn_name[n.name].append(n.id)
    by_class_name: dict[str, list[str]] = defaultdict(list)
    for c in classes.values():
        by_class_name[c.name].append(c.id)

    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str]] = set()

    def add(src: str, dst: str, kind: EdgeKind, weight: float = 1.0) -> None:
        if src != dst and (src, dst, kind.value) not in seen:
            seen.add((src, dst, kind.value))
            edges.append(GraphEdge(src=src, dst=dst, kind=kind, weight=weight))

    def resolve_name(
        name: str, src_module: str, index: dict[str, list[str]]
    ) -> list[str]:
        hits = index.get(name, [])
        same = [h for h in hits if h.split("::")[0] == src_module]
        return same or hits

    for n in fns.values():
        for callee in n.callees:
            for dst in resolve_name(callee, n.module, by_fn_name):
                add(n.id, dst, EdgeKind.CALLS)
        for cb in n.callback_names:
            for dst in resolve_name(cb, n.module, by_fn_name):
                add(n.id, dst, EdgeKind.CALLBACK_ARG)
        for t in n.param_types:
            for dst in resolve_name(t, n.module, by_class_name):
                add(n.id, dst, EdgeKind.REFERENCES_TYPE)

    for c in classes.values():
        # contains: class -> its methods (by id prefix)
        prefix = f"{c.id}."
        for fid in fns:
            if fid.startswith(prefix):
                add(c.id, fid, EdgeKind.CONTAINS)
        # inherits + overrides
        base_ids = [
            b for bn in c.bases for b in resolve_name(bn, c.module, by_class_name)
        ]
        for bid in base_ids:
            add(c.id, bid, EdgeKind.INHERITS)
        for mname in c.method_names:
            mid = f"{c.id}.{mname}"
            if mid not in fns:
                continue
            for bid in base_ids:
                base_method = f"{bid}.{mname}"
                if base_method in fns:
                    add(mid, base_method, EdgeKind.OVERRIDES)
    return edges
