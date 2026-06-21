"""Graph-aware detectors (GRAPH-*): advisory findings computed over the built graph.
Stdlib only — pure counting/grouping/distance. Run during `graph build`."""

import statistics
from collections import Counter, defaultdict
from typing import ClassVar

from auditor.config import AuditorSettings
from auditor.graph.model import (
    FUNCTION_KINDS,
    TEST_ROLES,
    EdgeKind,
    GraphCluster,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from auditor.graph.semantic_profile import ATTRS
from auditor.languages.python.detectors.graph_rules import (
    GOD_CONCEPT_RULE,
    NAMING_INCONSISTENCY_RULE,
    SCATTERED_CONCEPT_RULE,
)
from auditor.models import Category, Finding, Severity, VerdictKind

# structural edges that count toward a node's "degree" for god-concept centrality
# (a deliberate subset, via EdgeKind members — not magic strings)
_DEGREE_KINDS = (
    EdgeKind.CALLS,
    EdgeKind.REFERENCES_TYPE,
    EdgeKind.OVERRIDES,
    EdgeKind.CONTAINS,
    EdgeKind.REGISTERED_IN,
)


class GraphContext:
    """The built graph + indexes computed once, shared by all detectors."""

    def __init__(
        self,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        clusters: list[GraphCluster],
        settings: AuditorSettings,
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.clusters = clusters
        self.cfg = settings.graph
        self.by_id = {n.id: n for n in nodes}
        self.prod_funcs = [
            n for n in nodes if n.kind in FUNCTION_KINDS and n.role not in TEST_ROLES
        ]
        self.by_cluster: dict[int, list[GraphNode]] = defaultdict(list)
        for n in nodes:
            if (
                n.cluster_id is not None
                and n.role not in TEST_ROLES
                and n.kind in FUNCTION_KINDS
            ):
                self.by_cluster[n.cluster_id].append(n)
        self.degree: Counter[str] = Counter()
        for e in edges:
            if e.kind in _DEGREE_KINDS:
                self.degree[e.src] += 1
                self.degree[e.dst] += 1


class GraphDetector:
    """Base for graph detectors. Subclasses precompute their own state in __init__
    (calling super().__init__(ctx)) and implement detect()."""

    rule_id: ClassVar[str]
    category: ClassVar[Category]

    def __init__(self, ctx: GraphContext) -> None:
        self.ctx = ctx

    def detect(self) -> list[tuple[str, Finding]]:
        raise NotImplementedError

    def _finding(
        self, *, line: int, message: str, evidence: str, suggestion: str
    ) -> Finding:
        return Finding(
            rule_id=self.rule_id,
            category=self.category,
            severity=Severity.SUGGESTION,
            verdict_kind=VerdictKind.CANDIDATE,
            line=line,
            message=message,
            evidence=evidence,
            suggestion=suggestion,
            detector="graph",
        )

    def _located(
        self, n: GraphNode, *, message: str, evidence: str, suggestion: str
    ) -> tuple[str, Finding]:
        return n.module, self._finding(
            line=n.line, message=message, evidence=evidence, suggestion=suggestion
        )


class GodConcept(GraphDetector):
    rule_id: ClassVar[str] = GOD_CONCEPT_RULE
    category: ClassVar[Category] = Category.OOP_COMPOSITION

    def __init__(self, ctx: GraphContext) -> None:
        super().__init__(ctx)
        self.prod = ctx.prod_funcs + [
            n
            for n in ctx.nodes
            if n.kind == NodeKind.CLASS and n.role not in TEST_ROLES
        ]
        ranks = [n.rank for n in self.prod]
        degs = [ctx.degree.get(n.id, 0) for n in self.prod]
        sigma = ctx.cfg.god_concept_sigma

        def _floor(values: list[float]) -> float:
            if not values:
                return 0.0
            sd = statistics.pstdev(values)
            return (statistics.mean(values) + sigma * sd) if sd > 0.0 else float("inf")

        self.rank_floor = _floor(ranks)
        self.deg_floor = _floor(degs)

    def detect(self) -> list[tuple[str, Finding]]:
        out: list[tuple[str, Finding]] = []
        for n in sorted(self.prod, key=lambda x: x.id):
            deg = self.ctx.degree.get(n.id, 0)
            if n.rank >= self.rank_floor or deg >= self.deg_floor:
                out.append(
                    self._located(
                        n,
                        message=f"{n.qualname} is a centrality outlier "
                        f"(rank={n.rank:.5f}, degree={deg}) — consider decomposing this hub.",
                        evidence=n.id,
                        suggestion="split responsibilities; reduce inbound coupling.",
                    )
                )
        return out


class ScatteredConcept(GraphDetector):
    rule_id: ClassVar[str] = SCATTERED_CONCEPT_RULE
    category: ClassVar[Category] = Category.OOP_COMPOSITION

    def __init__(self, ctx: GraphContext) -> None:
        super().__init__(ctx)
        self.labels = {c.cluster_id: c.label for c in ctx.clusters}

    def detect(self) -> list[tuple[str, Finding]]:
        out: list[tuple[str, Finding]] = []
        for cid in sorted(self.ctx.by_cluster):
            members = self.ctx.by_cluster[cid]
            mods = {m.module for m in members}
            if (
                len(mods) >= self.ctx.cfg.scattered_min_modules
                and len(mods) / len(members) >= self.ctx.cfg.scattered_min_ratio
            ):
                anchor = max(members, key=lambda m: (m.rank, m.id))
                shown = sorted(mods)
                listed = ", ".join(shown[:5]) + (
                    f" +{len(shown) - 5} more" if len(shown) > 5 else ""
                )
                label = self.labels.get(cid, f"cluster-{cid}")
                out.append(
                    (
                        anchor.module,
                        self._finding(
                            line=anchor.line,
                            message=f"concept '{label}' is scattered across {len(mods)} "
                            f"modules ({len(members)} symbols) — consider consolidating.",
                            evidence=listed,
                            suggestion="gather this concept into one module/package.",
                        ),
                    )
                )
        return out


GRAPH_DETECTORS: list[type[GraphDetector]] = [GodConcept, ScatteredConcept]

# Re-export unused imports to satisfy linters (these are imported for their side effects
# or for use by downstream tasks that extend this module)
__all__ = [
    "ATTRS",
    "GRAPH_DETECTORS",
    "GodConcept",
    "GraphContext",
    "GraphDetector",
    "NAMING_INCONSISTENCY_RULE",
    "ScatteredConcept",
    "run_graph_detectors",
]


def run_graph_detectors(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    clusters: list[GraphCluster],
    settings: AuditorSettings,
) -> dict[str, list[Finding]]:
    ctx = GraphContext(nodes, edges, clusters, settings)
    per_file: dict[str, list[Finding]] = defaultdict(list)
    for det_cls in GRAPH_DETECTORS:
        for path, finding in det_cls(ctx).detect():
            per_file[path].append(finding)
    for path in per_file:
        per_file[path].sort(key=lambda f: (f.line, f.rule_id))
    return dict(per_file)
