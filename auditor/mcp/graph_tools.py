# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (MCP tool surface — JSON payloads by contract)
"""graph_* — the semantic-graph MCP tools. Owns the ``[graph]`` extra's optional-import guard;
these tools are only registered when the extra (numpy + scikit-learn) is installed."""

import time
from pathlib import Path

try:
    from auditor.graph.build import GraphBuilder
    from auditor.graph.query import GraphQuery

    _GRAPH_OK = True
except ImportError:  # the [graph] extra (numpy + scikit-learn) isn't installed
    _GRAPH_OK = False

from auditor.config import load_config
from auditor.database import IndexStore
from auditor.discovery import find_root
from auditor.engine import audit_target
from auditor.mcp.server import mcp
from auditor.paths import index_db_path, repo_key

if _GRAPH_OK:
    GRAPH_OVERRIDE: dict = {"graph": {"enabled": True}}

    @mcp.tool
    async def graph_build(path: str = ".", scan: bool = True) -> dict:
        """Build the semantic graph. By default it first runs a forced incremental scan (graph
        extraction on) so it works even if the repo never enabled the [graph] config — pass
        scan=False to build from existing cached facts only. Returns {nodes, edges, clusters,
        findings}."""
        root = find_root(Path(path))
        if scan:
            await audit_target(root, incremental=True, config_overrides=GRAPH_OVERRIDE)
        settings = load_config(root)
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            await index.repos.register(time.time())
            return await GraphBuilder().run(index, settings)

    @mcp.tool
    async def graph_related(
        symbol: str, path: str = ".", limit: int = 10
    ) -> list[dict]:
        """Top semantic neighbors (name + usage) of a symbol, ranked."""
        root = find_root(Path(path))
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            return await GraphQuery(index).related(symbol, limit=limit)

    @mcp.tool
    async def graph_neighbors(
        symbol: str, path: str = ".", depth: int = 1, limit: int = 25
    ) -> list[dict]:
        """Structural neighbors (calls/overrides/inherits/...) up to a depth. Capped at ``limit``
        (closest hops first) to keep responses small."""
        root = find_root(Path(path))
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            hits = await GraphQuery(index).neighbors(symbol, depth=depth)
        return hits[:limit]

    @mcp.tool
    async def graph_concept(term: str, path: str = ".", limit: int = 25) -> dict:
        """The concept cluster best matching a term. Members (rank-ordered) are capped at
        ``limit``; ``member_count`` is the true total. Returns {cluster_id, label, member_count,
        members, shown}."""
        root = find_root(Path(path))
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            concept = await GraphQuery(index).concept(term)
        if not concept:
            return {}
        members = concept.get("members", [])
        capped = members[:limit]
        return {
            "cluster_id": concept["cluster_id"],
            "label": concept["label"],
            "member_count": len(members),
            "members": capped,
            "shown": len(capped),
        }

    @mcp.tool
    async def graph_clusters(path: str = ".") -> list[dict]:
        """List concept clusters (label + size), largest first."""
        root = find_root(Path(path))
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            return await GraphQuery(index).clusters()

    @mcp.tool
    async def graph_search(term: str, path: str = ".", limit: int = 20) -> list[dict]:
        """Find graph symbols whose id contains ``term`` (case-insensitive), highest-rank
        first. Use to locate the exact symbol name before graph_usages/graph_neighbors.
        """
        root = find_root(Path(path))
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            return await GraphQuery(index).search(term, limit=limit)

    @mcp.tool
    async def graph_usages(symbol: str, path: str = ".", sample: int = 5) -> dict:
        """How a symbol is used/connected: structural edges grouped by kind with FULL counts
        and a rank-ordered sample, split into ``used_by`` (incoming — who depends on it) and
        ``depends_on`` (outgoing). Same-named symbols are disambiguated via ``ambiguous`` (the
        highest-rank match is used). Returns {} if not found. Prefer this over graph_neighbors
        for 'how is X used' — neighbors truncates silently with no totals."""
        root = find_root(Path(path))
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            return await GraphQuery(index).usages(symbol, sample=sample)

    @mcp.tool
    async def graph_overview(path: str = ".") -> dict:
        """One compact call to orient: counts, the largest clusters, and the worst graph hubs.
        Returns {nodes, edges, clusters, top_clusters, god_concepts, bottlenecks}. If the graph
        isn't built yet (0 nodes), the counts are zero and the lists empty — no error.
        """
        root = find_root(Path(path))
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            nodes = await index.graph.nodes()
            edges = await index.graph.all_edges()
            clusters = await index.graph.clusters()
            findings = await index.findings.by_rule_prefix("GRAPH-")
        god_concepts: list[str] = []
        bottlenecks: list[str] = []
        for f in findings:
            if f["rule_id"] != "GRAPH-GOD-CONCEPT":
                continue
            if "bottleneck" in f["message"]:
                bottlenecks.append(f["evidence"])
            elif "fan-out" in f["message"]:
                god_concepts.append(f["evidence"])
        return {
            "nodes": len(nodes),
            "edges": len(edges),
            "clusters": len(clusters),
            "top_clusters": [
                {"label": c["label"], "size": c["member_count"]} for c in clusters[:8]
            ],
            "god_concepts": god_concepts[:5],
            "bottlenecks": bottlenecks[:5],
        }
