# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (MCP tool surface — JSON payloads by contract)
"""graph_* — the semantic-graph MCP tools. The graph libraries are core dependencies, so these
tools register unconditionally."""

import time
from collections import defaultdict
from pathlib import Path

from fastmcp.exceptions import ToolError
from loguru import logger

from auditor.discovery import find_root
from auditor.graph.build import GraphBuilder
from auditor.graph.detectors import GodConceptKind
from auditor.graph.flow import FlowDirection, FlowOptions
from auditor.graph.model import (
    DEFAULT_FLOW_DEPTH,
    DEFAULT_FLOW_LIMIT,
    QUEUE_ROW_LIMIT,
    CallForm,
    EdgeKind,
    UnresolvedReason,
    enum_values,
)
from auditor.graph.payloads import NeighborsReport, QueueRowPayload
from auditor.graph.query import GraphQuery
from auditor.graph.refine.lock import RebuildLockTimeout
from auditor.graph.scan import autoscan
from auditor.mcp.helpers import (
    MUTATING,
    READ_ONLY,
    tool_config,
    tool_repo,
    tool_repo_at,
)
from auditor.mcp.server import mcp


@mcp.tool(annotations=MUTATING)
async def graph_build(path: str = ".", scan: bool = True) -> dict:
    """Build the semantic graph. By default it first runs a forced incremental scan (graph
    extraction on) so it works even if the repo never enabled the [graph] config; pass
    scan=False to build from existing cached facts only. Returns {nodes, edges, clusters,
    unresolved, findings, refined, expired}."""
    root = find_root(Path(path))
    # before the scan, which loads the config itself: a broken one is one line either way
    settings = tool_config(root)
    if scan:
        await autoscan(root)
    async with tool_repo_at(root) as repo:
        await repo.index.repos.register(time.time())
        try:
            report = await GraphBuilder().rebuild(
                repo.index,
                settings,
                timeout=settings.graph.rebuild_lock_timeout_seconds,
            )
        except RebuildLockTimeout as exc:
            raise ToolError(exc.advice) from exc
    return report.model_dump(mode="json")


@mcp.tool(annotations=READ_ONLY)
async def graph_related(symbol: str, path: str = ".", limit: int = 10) -> list[dict]:
    """Top semantic neighbors (name + usage) of a symbol, ranked."""
    async with tool_repo(path) as repo:
        return (await GraphQuery(repo.index).related(symbol, limit=limit)).model_dump(
            mode="json"
        )


@mcp.tool(annotations=READ_ONLY)
async def graph_neighbors(
    symbol: str, path: str = ".", depth: int = 1, limit: int = 25
) -> list[dict]:
    """Structural neighbors (calls/overrides/inherits/...) up to a depth. Capped at ``limit``
    (closest hops first) to keep responses small."""
    async with tool_repo(path) as repo:
        hits = await GraphQuery(repo.index).neighbors(symbol, depth=depth)
    return NeighborsReport(hits.root[:limit]).model_dump(mode="json")


@mcp.tool(annotations=READ_ONLY)
async def graph_concept(term: str, path: str = ".", limit: int = 25) -> dict:
    """The concept cluster best matching a term. Members (rank-ordered) are capped at
    ``limit``; ``member_count`` is the true total. Returns {cluster_id, label, member_count,
    members, shown}."""
    async with tool_repo(path) as repo:
        concept = await GraphQuery(repo.index).concept(term)
    return concept.capped(limit).model_dump(mode="json") if concept else {}


@mcp.tool(annotations=READ_ONLY)
async def graph_clusters(path: str = ".") -> list[dict]:
    """List concept clusters (label + size), largest first."""
    async with tool_repo(path) as repo:
        return (await GraphQuery(repo.index).clusters()).model_dump(mode="json")


@mcp.tool(annotations=READ_ONLY)
async def graph_search(term: str, path: str = ".", limit: int = 20) -> list[dict]:
    """Find graph symbols whose id contains ``term`` (case-insensitive), highest-rank
    first. Use to locate the exact symbol name before graph_usages/graph_neighbors.
    """
    async with tool_repo(path) as repo:
        return (await GraphQuery(repo.index).search(term, limit=limit)).model_dump(
            mode="json"
        )


@mcp.tool(annotations=READ_ONLY)
async def graph_usages(symbol: str, path: str = ".", sample: int = 5) -> dict:
    """How a symbol is used/connected: structural edges grouped by kind with FULL counts
    and a rank-ordered sample, split into ``used_by`` (incoming: who depends on it) and
    ``depends_on`` (outgoing). Same-named symbols are disambiguated via ``ambiguous`` (the
    highest-rank match is used). Returns {} if not found. Prefer this over graph_neighbors
    for 'how is X used': neighbors truncates silently with no totals."""
    async with tool_repo(path) as repo:
        usages = await GraphQuery(repo.index).usages(symbol, sample=sample)
    return usages.model_dump(mode="json") if usages else {}


@mcp.tool(annotations=READ_ONLY)
async def graph_flow(
    symbol: str,
    path: str = ".",
    direction: str = "out",
    depth: int = DEFAULT_FLOW_DEPTH,
    limit: int = DEFAULT_FLOW_LIMIT,
    kinds: list[str] | None = None,
    include_tests: bool = False,
    expand_hubs: bool = False,
    stop_at: list[str] | None = None,
) -> dict:
    """Read a code path from a symbol as a nested tree, instead of chaining graph_neighbors
    calls. Outward (direction="out") follows calls and callback_arg, expanding a base method's
    overriders and a symbol's registry as ``dispatches_to``; direction="in" reverses it into
    "what reaches this". Returns {symbol, resolved, ambiguous, root, direction, modules,
    truncated, limit}, or {} if the symbol isn't in the graph. ``modules``, the ordered list of
    modules the path touches, is usually the architecture answer. Nodes carry ``hub`` as
    {count, kind, collapsed} when the node's fan crossed the hub floor, ``collapsed`` true only
    where the hub rule refused to expand it (never at the start symbol, under expand_hubs, or on
    the last level the depth budget reached), ``seen_ref``/``cycle`` when the walk already covered
    them, ``stopped`` when a stop glob cut the branch, and ``unresolved`` for calls the resolver
    could not place. ``limit`` counts emitted nodes and is clamped to 1..1000 (the default of
    200 is roughly 50 KB of compact JSON) and ``depth`` is clamped to 0..64. Prune a wide tree
    with ``stop_at`` (module globs, the branch is shown and not entered) rather than by lowering
    ``depth``; ``kinds`` follows extra edge kinds on top of calls/callback_arg and is validated,
    ``include_tests`` keeps test symbols, ``expand_hubs`` opens the nodes the hub rule
    collapsed."""
    async with tool_repo(path) as repo:
        payload = await GraphQuery(repo.index).flow(
            symbol,
            FlowOptions.of(
                direction=FlowDirection(direction),
                depth=depth,
                limit=limit,
                kinds=enum_values(kinds, EdgeKind, "kinds") or (),
                include_tests=include_tests,
                expand_hubs=expand_hubs,
                stop_at=stop_at or (),
                hub_fan_in=repo.settings.graph.flow_hub_fan_in,
            ),
        )
    return payload.model_dump(mode="json") if payload else {}


@mcp.tool(annotations=READ_ONLY)
async def graph_overview(path: str = ".") -> dict:
    """One compact call to orient: counts, the largest clusters, and the worst graph hubs.
    Returns {nodes, edges, clusters, top_clusters, god_concepts, god_concept_count,
    bottlenecks, bottleneck_count}. The two hub lists are capped at 5 and the counts are the
    totals. If the graph isn't built yet (0 nodes), the counts are zero and the lists empty,
    not an error. A subkind neither hub list names is logged as a warning, so the two counts need
    not add up to the finding count.
    """
    async with tool_repo(path) as repo:
        nodes = await repo.index.graph.nodes()
        edges = await repo.index.graph.all_edges()
        clusters = await repo.index.graph.clusters()
        findings = await repo.index.findings.by_rule_prefix("GRAPH-GOD-CONCEPT")
    by_kind: dict[str, list[str]] = defaultdict(list)
    for f in findings:
        by_kind[f.subkind or ""].append(f.evidence)
    unclassified = sorted(set(by_kind) - {k.value for k in GodConceptKind})
    if unclassified:
        logger.warning(
            "graph_overview: unclassified GRAPH-GOD-CONCEPT subkinds {}", unclassified
        )
    god_concepts = by_kind.get(GodConceptKind.FAN_OUT, [])
    bottlenecks = by_kind.get(GodConceptKind.BOTTLENECK, [])
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "clusters": len(clusters),
        "top_clusters": [
            {"label": c["label"], "size": c["member_count"]} for c in clusters[:8]
        ],
        "god_concepts": god_concepts[:5],
        "god_concept_count": len(god_concepts),
        "bottlenecks": bottlenecks[:5],
        "bottleneck_count": len(bottlenecks),
    }


@mcp.tool(annotations=READ_ONLY)
async def graph_unresolved(
    path: str = ".",
    reason: list[str] | None = None,
    call_form: list[str] | None = None,
    limit: int = QUEUE_ROW_LIMIT,
    external: bool = True,
) -> list[dict]:
    """Facts the deterministic resolver could not place, worst first: ambiguous names, then
    ``self``/bare calls, then attribute calls, then label and cluster reasons. Filter with
    ``reason`` (ambiguous_name | unimportable_name | text_sparse | generic_label |
    singleton_cluster) and ``call_form`` (bare | self | attr), both repeatable lists; an unknown
    value is an error. Bare and self rows are the ones a reader can settle from one file. A row
    means the graph lost an edge, not that a symbol is unused, so read it before trusting an
    empty ``used_by`` from graph_usages. Rows with ``externally_bound`` name a non-repo import,
    sort last and are display only; pass external=false to drop them. ``definers`` and
    ``candidates`` are capped, with the true totals in ``definers_count`` /
    ``candidates_count``. Empty until graph_build has run."""
    async with tool_repo(path) as repo:
        rows = await repo.index.graph.unresolved(
            reasons=enum_values(reason, UnresolvedReason, "reason"),
            call_forms=enum_values(call_form, CallForm, "call_form"),
            limit=max(1, limit),
            external=external,
        )
    return [QueueRowPayload.of(r).model_dump(mode="json") for r in rows]
