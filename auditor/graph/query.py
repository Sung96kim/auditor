"""Read-side query API over the persisted graph (spec §10). Stdlib only."""

from collections import Counter

_STRUCTURAL = [
    "calls",
    "overrides",
    "inherits",
    "references_type",
    "callback_arg",
    "registered_in",
    "contains",
    "imports",
]
_SEMANTIC = ["name_similar", "usage_similar"]


class GraphQuery:
    def __init__(self, index) -> None:
        self.index = index

    async def _resolve_all(self, symbol: str) -> list[str]:
        """Every node id matching ``symbol`` — exact id, or a ``.name``/``::name`` suffix —
        sorted. A bare name can legitimately match several nodes (same-named symbols)."""
        if await self.index.graph.node(symbol):
            return [symbol]
        return sorted(
            n["node_id"]
            for n in await self.index.graph.nodes()
            if n["node_id"] == symbol
            or n["node_id"].endswith(f"::{symbol}")
            or n["node_id"].endswith(f".{symbol}")
        )

    async def _resolve(self, symbol: str) -> str | None:
        matches = await self._resolve_all(symbol)
        return matches[0] if matches else None

    async def related(self, symbol: str, limit: int = 10) -> list[dict]:
        nid = await self._resolve(symbol)
        if nid is None:
            return []
        nodes = await self.index.graph.nodes()
        ranks = {n["node_id"]: n["rank"] for n in nodes}
        kinds = {n["node_id"]: n["kind"] for n in nodes}
        out = []
        for e in await self.index.graph.edges_of(nid, _SEMANTIC):
            other = e["dst"] if e["src"] == nid else e["src"]
            out.append(
                {
                    "id": other,
                    "kind": kinds.get(other, "?"),
                    "weight": round(e["weight"], 4),
                    "rank": round(ranks.get(other, 0.0), 6),
                }
            )
        out.sort(key=lambda r: (-r["weight"], -r["rank"], r["id"]))
        return out[:limit]

    async def neighbors(self, symbol: str, depth: int = 1) -> list[dict]:
        nid = await self._resolve(symbol)
        if nid is None:
            return []
        kinds = {n["node_id"]: n["kind"] for n in await self.index.graph.nodes()}
        seen = {nid}
        frontier = [nid]
        out: list[dict] = []
        for hop in range(1, depth + 1):
            nxt: list[str] = []
            for cur in frontier:
                for e in await self.index.graph.edges_of(cur, _STRUCTURAL):
                    other, direction = (
                        (e["dst"], "out") if e["src"] == cur else (e["src"], "in")
                    )
                    if other not in seen:
                        seen.add(other)
                        nxt.append(other)
                        out.append(
                            {
                                "id": other,
                                "kind": kinds.get(other, "?"),
                                "edge": e["kind"],
                                "direction": direction,
                                "hops": hop,
                            }
                        )
            frontier = nxt
        return out

    async def search(self, term: str, limit: int = 20) -> list[dict]:
        """Find symbols whose id contains ``term`` (case-insensitive), highest-rank first —
        for locating the exact node before a usages/neighbors query."""
        term_l = term.lower()
        hits = [
            n for n in await self.index.graph.nodes() if term_l in n["node_id"].lower()
        ]
        hits.sort(key=lambda n: (-(n.get("rank") or 0.0), n["node_id"]))
        return [
            {
                "id": n["node_id"],
                "kind": n["kind"],
                "rank": round(n.get("rank") or 0.0, 6),
            }
            for n in hits[:limit]
        ]

    async def usages(self, symbol: str, sample: int = 5) -> dict:
        """How ``symbol`` connects: structural edges grouped by kind with full counts and a
        rank-ordered sample, split into ``used_by`` (incoming — who depends on it) and
        ``depends_on`` (outgoing). Picks the highest-rank node when a name is ambiguous and
        lists the rest under ``ambiguous``. ``{}`` if the symbol isn't found."""
        matches = await self._resolve_all(symbol)
        if not matches:
            return {}
        nodes = await self.index.graph.nodes()
        rank = {n["node_id"]: (n.get("rank") or 0.0) for n in nodes}
        kind_of = {n["node_id"]: n["kind"] for n in nodes}
        primary = max(matches, key=lambda nid: rank.get(nid, 0.0))

        used_by: dict[str, list[str]] = {}
        depends_on: dict[str, list[str]] = {}
        for e in await self.index.graph.edges_of(primary, _STRUCTURAL):
            incoming = e["dst"] == primary
            other = e["src"] if incoming else e["dst"]
            bucket = used_by if incoming else depends_on
            bucket.setdefault(e["kind"], []).append(other)

        def summarize(b: dict[str, list[str]]) -> dict[str, dict]:
            out = {}
            for k, others in b.items():
                uniq = sorted(set(others), key=lambda o: (-rank.get(o, 0.0), o))
                out[k] = {"count": len(uniq), "sample": uniq[:sample]}
            return out

        used = summarize(used_by)
        deps = summarize(depends_on)
        return {
            "symbol": symbol,
            "resolved": primary,
            "kind": kind_of.get(primary),
            "ambiguous": [m for m in matches if m != primary],
            "used_by": used,
            "depends_on": deps,
            "total_in": sum(v["count"] for v in used.values()),
            "total_out": sum(v["count"] for v in deps.values()),
        }

    async def clusters(self) -> list[dict]:
        return await self.index.graph.clusters()

    async def concept(self, term: str) -> dict:
        """The concept cluster best matching ``term`` — by label first, else by the cluster
        with the most members whose name contains the term. Returns ``{}`` when nothing
        matches (rather than falling back to the largest cluster)."""
        clusters = await self.index.graph.clusters()
        if not clusters:
            return {}
        term_l = term.lower()
        label_hits = [
            c
            for c in clusters
            if term_l in c["label"].lower() or c["label"].lower() in term_l
        ]
        if label_hits:
            best = max(label_hits, key=lambda c: c["member_count"])
        else:
            # no label match: rank clusters by how many of their members' names contain the
            # term, so e.g. "submission" finds the cluster full of submission-named symbols
            counts: Counter[int] = Counter()
            for n in await self.index.graph.nodes():
                cid = n.get("cluster_id")
                if cid is not None and term_l in (n.get("name") or "").lower():
                    counts[cid] += 1
            if not counts:
                return {}
            best_id = counts.most_common(1)[0][0]
            best = next((c for c in clusters if c["cluster_id"] == best_id), None)
            if best is None:
                return {}
        members = await self.index.graph.cluster_members(best["cluster_id"])
        return {
            "cluster_id": best["cluster_id"],
            "label": best["label"],
            "members": members,
        }
