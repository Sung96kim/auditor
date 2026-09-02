"""Spec 11's tuning trial: one facts-only rebuild that is never written, measured against the
graph this checkout already holds, and the proposal that asks for it.

Separate from `tuning.py` because `build.py` imports the precedence read and this imports
`build.py`; one module for both would be a cycle.
"""

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from auditor.config import AuditorSettings, GraphConfig
from auditor.database import IndexStore
from auditor.graph.build import GraphBuilder, GraphWrite, tuned_settings
from auditor.graph.cluster import modularity
from auditor.graph.model import EdgeKind, GraphEdge
from auditor.graph.refine.models import (
    ClientKind,
    ProducerKind,
    Refinement,
    RefinementKind,
    RefinementOutcome,
    RefinementStatus,
    Run,
    RunnerKind,
    RunOutcome,
    RunStatus,
    TriggerKind,
    TuningBaseline,
    TuningMetrics,
    TuningRow,
    TuningStatus,
)
from auditor.graph.refine.service import RefinementService
from auditor.graph.refine.tuning import (
    LIVE_STATUSES,
    MEASURE_FROM,
    PROPOSAL_WINDOW_SECONDS,
    TuningLedger,
    TuningRefused,
    check_cap,
    confirmation_token,
    knob,
    row_token,
    stopword,
)
from auditor.user_settings import UserSettings

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


class TuningService(BaseModel):
    """Spec 11's producer side: propose one knob change, and measure the trial it asks for.

    Proposing and measuring are two calls on purpose. A facts-only rebuild on this repo is 19 to
    41 seconds measured, which is a background job and not something an agent waits on inside a
    tool call (S11 P6).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    service: RefinementService

    @property
    def user(self) -> UserSettings:
        """The one settings object: the service holds it, so the two can never disagree (M-6)."""
        return self.service.user

    @property
    def ledger(self) -> TuningLedger:
        return TuningLedger(index=self.service.index)

    async def propose(
        self,
        key: str,
        value: object,
        reason: str,
        *,
        producer: ProducerKind = ProducerKind.AGENT,
        client: ClientKind = ClientKind.CLI,
        session_id: str | None = None,
        agent_name: str | None = None,
        now: float | None = None,
    ) -> TuningRow:
        """Record one knob change as a `pending` row under its own `tune` run (spec 8.3 item 5).

        Raises:
            TuningRefused: tuning is off, the key or the value is not allow-listed, the cap is
                full, the token is already proposed or active, or one proposal for this key was
                already recorded inside the last day.
        """
        if self.user.observer.tuning.mode == "off":
            raise TuningRefused(
                "tuning is off; set observer.tuning.mode to 'propose' to record proposals"
            )
        if not reason.strip():
            raise TuningRefused("a tuning proposal needs a reason")
        knob(key)
        token = stopword(value)
        moment = time.time() if now is None else now
        rows = await self.ledger.rows()
        _rate_limited(rows, key, moment)
        superseded = _clashes(rows, token, self.user.observer.tuning.stopwords_max)
        run_id = await self._tune_run(
            key, moment, producer, client, session_id, agent_name
        )
        tuning_id = await self.service.index.tuning.supersede_and_add(
            [r.tuning_id for r in superseded],
            TuningRow(
                repo_identity=self.service.index.partition.identity,
                key=key,
                value_json=json.dumps(token),
                token=confirmation_token(),
                run_id=run_id,
                reason=reason.strip(),
                created_at=moment,
            ),
        )
        return await self.ledger.row(str(tuning_id))

    async def _tune_run(
        self,
        key: str,
        moment: float,
        producer: ProducerKind,
        client: ClientKind,
        session_id: str | None,
        agent_name: str | None,
    ) -> str:
        """Invariant 2's row for a proposal: `trigger_kind=tune`, `runner=none`, closed at once.

        Written directly rather than through `service.begin`, the way spec 5.7's auto-retire is:
        a proposal stages nothing, so it has no reason to hold a slot in the run registry and no
        reason to be able to evict a run that does (S11 L8). The note is a summary, because
        `graph log` paints `error` red.
        """
        run_id = await self.service.index.runs.add_run(
            Run(
                repo_identity=self.service.index.partition.identity,
                client=client,
                producer=producer,
                runner=RunnerKind.NONE,
                trigger_kind=TriggerKind.TUNE,
                session_id=session_id,
                agent_name=agent_name,
                status=RunStatus.QUEUED,
                started_at=moment,
            )
        )
        await self.service.index.runs.finish_run(
            run_id,
            RunOutcome.of(
                RunStatus.SUCCEEDED,
                summary=f"tuning proposal for {key}",
                finished_at=moment,
            ),
        )
        return run_id

    async def measure(self, tuning_id: int, *, now: float | None = None) -> Trial:
        """Run this proposal's trial and write its verdict onto the row (spec 11).

        A passing trial leaves the row `pending`; a guard that refused, or a checkout with no
        graph to compare against, lands it `rejected` so nothing measures it a second time.

        Raises:
            TuningRefused: the row is not one a trial may look at, which is every status but
                `pending` and `rejected`.
        """
        row = await self.ledger.row(str(tuning_id))
        if row.status not in MEASURE_FROM:
            raise TuningRefused(
                f"tuning {row.tuning_id} is {row.status.value}; only "
                f"{sorted(s.value for s in MEASURE_FROM)} rows can be measured"
            )
        moment = time.time() if now is None else now
        active = await self.service.index.refinements.active()
        base, name_edges, labels = await baseline_of(
            self.service.index, self.service.settings.graph, active
        )
        if not base.clusters:
            return await self._refuse(row, NO_GRAPH, moment)
        settings = await tuned_settings(
            self.service.index, self.service.settings, extra=(row_token(row),)
        )
        # the detectors' findings are discarded with the write, so a trial does not pay for them
        settings = settings.model_copy(
            update={"graph": settings.graph.model_copy(update={"detect": False})}
        )
        write = await asyncio.to_thread(_shaped, self.service.index, settings)
        trial = measured(
            write,
            active,
            base,
            name_edges,
            labels,
            cfg=self.service.settings.graph,
            now=moment,
        )
        await self.ledger.record(row.tuning_id, trial.metrics, trial.status)
        return trial

    async def _refuse(self, row: TuningRow, why: str, now: float) -> Trial:
        """Record one refusal a rebuild cannot answer, so the loop stops asking (S11 E4)."""
        trial = Trial(metrics=TuningMetrics(measured_at=now, refused=why))
        await self.ledger.record(row.tuning_id, trial.metrics, trial.status)
        return trial

    async def unmeasured(self) -> TuningRow | None:
        """The oldest `pending` row no trial has measured yet, which is the loop's work item."""
        pending = await self.ledger.rows(statuses=[TuningStatus.PENDING])
        waiting = [r for r in pending if not r.metrics.measured_at]
        return waiting[0] if waiting else None


def _rate_limited(rows: Sequence[TuningRow], key: str, now: float) -> None:
    """Spec 11's one proposal per key per day, counted from the newest live row for that key.

    Reverted and superseded rows do not count: a proposal that was taken back out has no claim on
    the key it named (S11 L4).
    """
    recent = [
        r
        for r in rows
        if r.key == key
        and r.status in LIVE_STATUSES
        and now - r.created_at < PROPOSAL_WINDOW_SECONDS
    ]
    if recent:
        newest = max(r.created_at for r in recent)
        hours = (PROPOSAL_WINDOW_SECONDS - (now - newest)) / 3600
        raise TuningRefused(
            f"one {key} proposal per day; the next one is in {hours:.1f} hours"
        )


def _clashes(rows: Sequence[TuningRow], token: str, cap: int) -> list[TuningRow]:
    """The pending rows this proposal supersedes, refusing when the token is live or the cap full."""
    same = [r for r in rows if r.key == "stopwords" and row_token(r) == token]
    if any(r.status is TuningStatus.ACTIVE for r in same):
        raise TuningRefused(f"{token!r} is already an active stopword")
    check_cap([r for r in rows if r.status is TuningStatus.ACTIVE], cap)
    return [r for r in same if r.status is TuningStatus.PENDING]
