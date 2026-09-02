"""Spec 11's tuning trial: one facts-only rebuild that is never written, measured against the
graph this checkout already holds, and the proposal that asks for it.

Separate from `tuning.py` because `build.py` imports the precedence read and this imports
`build.py`; one module for both would be a cycle.
"""

import asyncio
from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from auditor.config import AuditorSettings, GraphConfig
from auditor.database import IndexStore
from auditor.graph.build import GraphBuilder, GraphWrite
from auditor.graph.cluster import modularity
from auditor.graph.model import EdgeKind, GraphEdge
from auditor.graph.refine.models import (
    Refinement,
    RefinementKind,
    RefinementOutcome,
    RefinementStatus,
    TuningBaseline,
    TuningMetrics,
    TuningStatus,
)

#: spec 11's size-distribution guard: a trial may move the cluster count by at most this share
CLUSTER_BAND: Final[float] = 0.2

#: the cluster-shaped refinement kinds a trial's clustering can strand
_CLUSTER_KINDS = frozenset({RefinementKind.RELABEL_CLUSTER, RefinementKind.MOVE_NODE})

#: what a trial that found no graph to compare against records, once, instead of rebuilding
NO_GRAPH = (
    "this checkout holds no built graph, so there is nothing to measure against; "
    "run `auditr graph build` and measure again"
)


class Trial(BaseModel):
    """One measured trial: the metrics the row stores and the guard that refused it, if any."""

    model_config = ConfigDict(frozen=True)

    metrics: TuningMetrics = TuningMetrics()

    @property
    def refused(self) -> str:
        return self.metrics.refused

    @property
    def passed(self) -> bool:
        return not self.metrics.refused

    @property
    def status(self) -> TuningStatus:
        """Where this verdict leaves the row: waiting for a human, or refused by a guard."""
        return TuningStatus.PENDING if self.passed else TuningStatus.REJECTED


def cluster_shape(
    node_ids: Sequence[str],
    sizes: Sequence[int],
    edges: Sequence[GraphEdge],
    assignment: Mapping[str, int],
    *,
    floor: float,
    stranded: int,
) -> TuningBaseline:
    """One clustering's five guard numbers, computed one way for both sides of a trial.

    Shared so the stored graph and the trial's write cannot disagree about what a singleton or a
    top-cluster share is (S11 M6).
    """
    total = sum(sizes) or len(node_ids) or 1
    return TuningBaseline(
        modularity=modularity(node_ids, edges, assignment, floor=floor),
        clusters=len(sizes),
        singletons=sum(1 for s in sizes if s == 1),
        top_cluster_share=(max(sizes) / total if sizes else 0.0),
        stranded_pins=stranded,
    )


async def baseline_of(
    index: IndexStore, cfg: GraphConfig, active: Sequence[Refinement]
) -> tuple[TuningBaseline, int, frozenset[str]]:
    """The stored graph's guard numbers, its `name_similar` edge count and its cluster labels.

    Read, not rebuilt: spec 11 asks for two facts-only rebuilds and one of them is the graph this
    checkout already holds, which costs queries instead of half a minute (S11 P5).
    """
    rows = await index.graph.clusters()
    edges = [GraphEdge.model_validate(e) for e in await index.graph.all_edges()]
    nodes = await index.graph.nodes()
    ids = [str(n["node_id"]) for n in nodes]
    base = cluster_shape(
        ids,
        [int(r["member_count"]) for r in rows],
        edges,
        {
            str(n["node_id"]): int(n["cluster_id"])
            for n in nodes
            if n["cluster_id"] is not None
        },
        floor=cfg.cluster_floor,
        stranded=sum(1 for r in _cluster_pins(active) if r.noop_builds),
    )
    name_edges = sum(1 for e in edges if e.kind is EdgeKind.NAME_SIMILAR)
    return base, name_edges, frozenset(str(r["label"]) for r in rows)


def measured(
    write: GraphWrite,
    active: Sequence[Refinement],
    base: TuningBaseline,
    base_name_edges: int,
    base_labels: frozenset[str],
    *,
    cfg: GraphConfig,
    now: float,
) -> Trial:
    """One trial's metrics and the first guard that refuses it (spec 11).

    Order matters: a stranded pin is reported before a size guard, because a lost pin is the one
    failure a human cannot undo by reverting the row.
    """
    shape = cluster_shape(
        [n.id for n in write.nodes],
        [c.member_count for c in write.clusters],
        write.edges,
        {n.id: n.cluster_id for n in write.nodes if n.cluster_id is not None},
        floor=cfg.cluster_floor,
        stranded=_stranded_pins(write.outcomes, active),
    )
    name_edges = sum(1 for e in write.edges if e.kind is EdgeKind.NAME_SIMILAR)
    labels = {c.label for c in write.clusters}
    metrics = TuningMetrics(
        modularity=shape.modularity,
        clusters=shape.clusters,
        singletons=shape.singletons,
        top_cluster_share=shape.top_cluster_share,
        stranded_pins=shape.stranded_pins,
        name_edge_churn=(
            abs(name_edges - base_name_edges) / base_name_edges
            if base_name_edges
            else 0.0
        ),
        label_churn=(
            len(base_labels - labels) / len(base_labels) if base_labels else 0.0
        ),
        measured_at=now,
        baseline=base,
    )
    return Trial(metrics=metrics.model_copy(update={"refused": _guard(metrics)}))


def _guard(m: TuningMetrics) -> str:
    """The first spec 11 guard this trial fails, named, or "" when it passes them all."""
    if m.stranded_pins:
        return f"{m.stranded_pins} pinned cluster refinement(s) would be stranded"
    if m.baseline.clusters and abs(m.clusters - m.baseline.clusters) > (
        CLUSTER_BAND * m.baseline.clusters
    ):
        return (
            f"cluster count {m.baseline.clusters} -> {m.clusters}, outside the "
            f"{CLUSTER_BAND:.0%} band"
        )
    if m.singletons > m.baseline.singletons:
        return f"singleton clusters {m.baseline.singletons} -> {m.singletons}"
    if m.top_cluster_share > m.baseline.top_cluster_share:
        return (
            f"top cluster share {m.baseline.top_cluster_share:.3f} -> "
            f"{m.top_cluster_share:.3f}"
        )
    return ""


def _cluster_pins(active: Sequence[Refinement]) -> list[Refinement]:
    """The pinned refinements a clustering can strand: the two kinds that name a cluster."""
    return [
        r
        for r in active
        if r.status is RefinementStatus.PINNED and r.kind in _CLUSTER_KINDS
    ]


def _stranded_pins(
    outcomes: Sequence[RefinementOutcome], active: Sequence[Refinement]
) -> int:
    """Pinned cluster refinements **this** clustering stranded, and not the ones already stranded.

    `Overlay._noop` is the only writer that advances the counter, so an outcome above the row's
    own count is a pin this pass looked for and could not place; a verdict from triage resets it
    to 0 and a carried verdict leaves it alone, so neither is counted (S11 E3).
    """
    pinned = {r.refinement_id: r.noop_builds for r in _cluster_pins(active)}
    return sum(
        1
        for o in outcomes
        if o.refinement_id in pinned
        and not o.applied
        and o.noop_builds > pinned[o.refinement_id]
    )


def _shaped(index: IndexStore, settings: AuditorSettings) -> GraphWrite:
    """One trial's rebuild, run to completion on whatever thread calls this.

    Called through `asyncio.to_thread` so the 19 to 41 seconds of sklearn and networkx stay off
    the loop the daemon drives every repo on (spec 11's worker thread).
    """
    return asyncio.run(GraphBuilder().shape(index, settings))
