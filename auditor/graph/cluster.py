"""Concept clustering: k-NN-sparsified graph + deterministic label propagation (spec §9e)."""

from collections import Counter, defaultdict

from auditor.graph.model import GraphEdge, GraphNode

_SEMANTIC = ("name_similar", "usage_similar")
_STRUCTURAL_W = {"calls": 0.5, "overrides": 0.5}


def cluster_concepts(
    nodes: list[GraphNode], edges: list[GraphEdge], *, floor: float = 0.45
) -> tuple[dict[str, int], dict[int, str]]:
    ids = [n.id for n in nodes]
    adj: dict[str, set[str]] = {i: set() for i in ids}
    present = set(ids)
    for e in edges:
        if e.src not in present or e.dst not in present:
            continue
        keep = (e.kind in _SEMANTIC and e.weight >= floor) or e.kind in _STRUCTURAL_W
        if keep:
            adj[e.src].add(e.dst)
            adj[e.dst].add(e.src)

    # deterministic label propagation: start each node in its own label (its sorted index),
    # then repeatedly adopt the smallest label among neighbors+self until stable.
    order = sorted(ids)
    label = {nid: i for i, nid in enumerate(order)}
    changed = True
    while changed:
        changed = False
        for nid in order:
            best = min([label[nid]] + [label[m] for m in adj[nid]])
            if best != label[nid]:
                label[nid] = best
                changed = True

    # renumber labels to a dense 0..k range (stable by first appearance in sorted order)
    remap: dict[int, int] = {}
    final: dict[str, int] = {}
    for nid in order:
        remap.setdefault(label[nid], len(remap))
        final[nid] = remap[label[nid]]

    members: dict[int, list[str]] = defaultdict(list)
    for nid, cid in final.items():
        members[cid].append(nid)
    toks_by_id = {n.id: n.doc_tokens for n in nodes}
    names: dict[int, str] = {}
    for cid, mem in members.items():
        counter: Counter[str] = Counter()
        for m in mem:
            counter.update(set(toks_by_id.get(m, ())))
        names[cid] = counter.most_common(1)[0][0] if counter else f"cluster-{cid}"
    return final, names
