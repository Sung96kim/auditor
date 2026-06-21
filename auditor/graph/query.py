"""Read-side query API over the persisted graph (spec §10). Stdlib only."""

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

    async def _resolve(self, symbol: str) -> str | None:
        if await self.index.graph.node(symbol):
            return symbol
        matches = [
            n["node_id"]
            for n in await self.index.graph.nodes()
            if n["node_id"] == symbol
            or n["node_id"].endswith(f"::{symbol}")
            or n["node_id"].endswith(f".{symbol}")
        ]
        return (
            matches[0]
            if len(matches) == 1
            else (sorted(matches)[0] if matches else None)
        )

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

    async def clusters(self) -> list[dict]:
        return await self.index.graph.clusters()

    async def concept(self, term: str) -> dict:
        clusters = await self.index.graph.clusters()
        term_l = term.lower()
        ranked = sorted(
            clusters,
            key=lambda c: (term_l not in c["label"].lower(), -c["member_count"]),
        )
        if not ranked:
            return {}
        best = ranked[0]
        members = await self.index.graph.cluster_members(best["cluster_id"])
        return {
            "cluster_id": best["cluster_id"],
            "label": best["label"],
            "members": members,
        }
