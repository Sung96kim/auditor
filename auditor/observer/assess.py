"""Spec 8.6's change assessment: does this edit batch warrant a refinement run?

Pure functions over frozen models. Nothing here reads a file, opens a store or looks at a clock:
the loop does the I/O and hands the results in, which is what lets one rule serve the daemon, the
tests and a probe.

`assess_unchanged` builds an `Assessment` rather than being a classmethod on it because the reason
literal is the observer's, not the model's: `graph/refine/models.py` is the shared vocabulary and
one gate's wording does not belong there.
"""

from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from auditor.graph.hashes import FileHashes, file_hashes, node_facts_sha, node_truth_sha
from auditor.graph.model import (
    CallForm,
    FileGraphFacts,
    GraphNode,
    UnresolvedReason,
    UnresolvedRow,
)
from auditor.graph.refine.models import (
    BOUNDED_FORMS,
    Assessment,
    AssessmentDecision,
    BatchKind,
    Decision,
    NodePair,
    Refinement,
    RefinementStatus,
)
from auditor.observer.budget import BudgetState
from auditor.user_settings import SchedulingConfig


class PathOutcome(StrEnum):
    """What stage 1 found for one path: spec 8.6's three table rows and the three it names in
    prose, cheapest first."""

    UNCHANGED = "unchanged"
    UNPARSED = "unparsed"
    FACTS_ONLY = "facts_only"
    TRUTH = "truth"
    ADDED = "added"
    REMOVED = "removed"


#: the outcomes whose facts the loop must write before it rebuilds; `REMOVED` writes by deleting
_PERSISTED = frozenset(
    {PathOutcome.FACTS_ONLY, PathOutcome.TRUTH, PathOutcome.ADDED, PathOutcome.REMOVED}
)


class NodeDigest(BaseModel):
    """One node's spec 5.5 pair, so a batch can name the nodes whose facts moved (P21)."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    truth: str
    facts: str

    @classmethod
    def of(cls, node: GraphNode) -> "NodeDigest":
        """The same two functions ``file_hashes`` rolls, at the granularity the field names."""
        return cls(
            node_id=node.id, truth=node_truth_sha(node), facts=node_facts_sha(node)
        )


class CachedFile(BaseModel):
    """What ``graph_facts`` held for one path, read before anything was written (spec 8.6)."""

    model_config = ConfigDict(frozen=True)

    content_hash: str | None = None
    hashes: FileHashes | None = None
    node_hashes: tuple[NodeDigest, ...] = ()

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(d.node_id for d in self.node_hashes)


class EditedFile(BaseModel):
    """One path of a batch as the loop read it.

    ``cached`` of ``None`` is a path the index has never seen; ``content_hash`` of ``None`` is a
    path that is gone; ``extracted`` of ``None`` is a path with no facts to compare, which is the
    same answer as a file that extracted to nothing.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    cached: CachedFile | None = None
    content_hash: str | None = None
    extracted: FileGraphFacts | None = None


class PathVerdict(BaseModel):
    """Stage 1's answer for one path: what moved, and whether the loop must write and rebuild."""

    model_config = ConfigDict(frozen=True)

    path: str
    outcome: PathOutcome
    added_nodes: tuple[str, ...] = ()
    removed_nodes: tuple[str, ...] = ()
    facts_changed_nodes: tuple[str, ...] = ()

    @property
    def persist(self) -> bool:
        return self.outcome in _PERSISTED


def _ids(facts: FileGraphFacts | None) -> tuple[str, ...]:
    return () if facts is None else tuple(n.id for n in facts.nodes)


def assess_path(edited: EditedFile) -> PathVerdict:
    """Classify one edited path against the facts cached for it (spec 8.6 stage 1).

    The content hash short circuit comes first, so a Stop path set's repeated dirty file costs a
    string comparison rather than a parse; a path with no facts to compare is `UNPARSED`, whether
    or not the index has seen it before.
    """
    cached, extracted = edited.cached, edited.extracted
    if edited.content_hash is None:
        return PathVerdict(
            path=edited.path,
            outcome=PathOutcome.REMOVED,
            removed_nodes=cached.node_ids if cached else (),
        )
    if cached is not None and cached.content_hash == edited.content_hash:
        return PathVerdict(path=edited.path, outcome=PathOutcome.UNCHANGED)
    # one rule for every shape of "no facts to compare": the loop never extracted, or `extract`
    # swallowed a SyntaxError and returned nothing, which a file that parses never does because
    # it always has at least its module node. Persisting either would write the file's nodes away
    if extracted is None or not extracted.nodes:
        return PathVerdict(path=edited.path, outcome=PathOutcome.UNPARSED)
    if cached is None:
        return PathVerdict(
            path=edited.path, outcome=PathOutcome.ADDED, added_nodes=_ids(extracted)
        )
    fresh = file_hashes(extracted.nodes)
    if cached.hashes == fresh:
        return PathVerdict(path=edited.path, outcome=PathOutcome.UNCHANGED)
    digests = {d.node_id: d for d in cached.node_hashes}
    now_digests = {n.id: NodeDigest.of(n) for n in extracted.nodes}
    was, now = frozenset(digests), frozenset(now_digests)
    # a half written cache pair degrades to a miss the way `GraphDB.hashes` does, and the safe
    # direction for a miss is to rebuild; equal truth rolls already imply equal id sets, because
    # `file_hashes` rolls `id:truth` pairs, so there is no id comparison to add here
    facts_only = cached.hashes is not None and cached.hashes.truth == fresh.truth
    return PathVerdict(
        path=edited.path,
        outcome=PathOutcome.FACTS_ONLY if facts_only else PathOutcome.TRUTH,
        added_nodes=tuple(sorted(now - was)),
        removed_nodes=tuple(sorted(was - now)),
        facts_changed_nodes=tuple(
            sorted(i for i in now & was if digests[i] != now_digests[i])
        ),
    )


class Stage1(BaseModel):
    """Stage 1 over a whole batch: the per path verdicts and the union of what they moved."""

    model_config = ConfigDict(frozen=True)

    verdicts: tuple[PathVerdict, ...] = ()

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(v.path for v in self.verdicts)

    @property
    def persist_paths(self) -> tuple[str, ...]:
        return tuple(v.path for v in self.verdicts if v.persist)

    @property
    def needs_rebuild(self) -> bool:
        """Whether one path moved enough to make the graph worth rebuilding (spec 8.6's table)."""
        return bool(self.persist_paths)

    @property
    def added_nodes(self) -> tuple[str, ...]:
        return self._union(lambda v: v.added_nodes)

    @property
    def removed_nodes(self) -> tuple[str, ...]:
        return self._union(lambda v: v.removed_nodes)

    @property
    def facts_changed_nodes(self) -> tuple[str, ...]:
        return self._union(lambda v: v.facts_changed_nodes)

    @property
    def changed_nodes(self) -> frozenset[str]:
        """Every node this batch touched, which is what an anchor is tested against (P10)."""
        return frozenset(
            self.added_nodes + self.removed_nodes + self.facts_changed_nodes
        )

    def _union(
        self, field: Callable[[PathVerdict], tuple[str, ...]]
    ) -> tuple[str, ...]:
        """One node set across every verdict, sorted and deduplicated, so a fourth is one line."""
        return tuple(sorted({i for v in self.verdicts for i in field(v)}))


def stage_one(edited: Sequence[EditedFile]) -> Stage1:
    """Classify a whole batch, one verdict per path, in the order the loop first listed them.

    A path listed twice is classified from its last read: a `PostToolUse` event and the Stop path
    set name the same file at different moments, and the later read is the settled one (P23). The
    caller owns stage 0, so anything handed here is classified, auditable or not.
    """
    seen: dict[str, EditedFile] = {}
    for one in edited:
        # a dict keeps the first insertion's position and the last value, so `files` still lists
        # the batch in arrival order while the verdict comes from the freshest read
        seen[one.path] = one
    return Stage1(verdicts=tuple(assess_path(e) for e in seen.values()))


#: the same two call forms `tiers` gates on, so the assessment and tier B cannot drift on what a
#: low budget still lets through (spec 10.1)
_BOUNDED_FORMS = frozenset(BOUNDED_FORMS)


class QueuePair(BaseModel):
    """One ``graph_unresolved`` row as the diff compares it (spec 8.6 stage 2).

    Only the columns the diff and the low budget rule read: the whole stored key, what the
    resolver offered, and the two flags that decide whether the row may count at all. ``reason``
    has no default because it is a key column, and one invented for it would merge two rows.
    """

    model_config = ConfigDict(frozen=True)

    node_id: str
    name: str
    reason: UnresolvedReason
    call_form: CallForm = CallForm.BARE
    candidates: tuple[str, ...] = ()
    definers: tuple[str, ...] = ()
    externally_bound: bool = False

    @property
    def key(self) -> tuple[str, str, UnresolvedReason]:
        """The store's own key, so two rows for one question never collapse into one (spec 5.6)."""
        return (self.node_id, self.name, self.reason)

    @property
    def pair(self) -> NodePair:
        return NodePair(node_id=self.node_id, name=self.name)

    @property
    def offer(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """What the resolver offered here; a move in it makes an existing row new (spec 8.6)."""
        return (self.candidates, self.definers)

    @classmethod
    def of(cls, row: UnresolvedRow) -> "QueuePair":
        """One stored row narrowed to what the assessment reads."""
        return cls(
            node_id=row.node_id,
            name=row.name,
            reason=row.reason,
            call_form=row.call_form,
            candidates=row.candidates,
            definers=row.definers,
            externally_bound=row.externally_bound,
        )


class RefinementState(BaseModel):
    """One refinement across a rebuild: its status and the nodes it is pinned to (spec 5.5)."""

    model_config = ConfigDict(frozen=True)

    refinement_id: int
    status: RefinementStatus
    anchor_nodes: tuple[str, ...] = ()

    @classmethod
    def of(cls, refinement: Refinement) -> "RefinementState":
        """Read off the stored row, whose ``anchored_ids`` already answers the anchor set."""
        return cls(
            refinement_id=refinement.refinement_id,
            status=refinement.status,
            anchor_nodes=refinement.anchored_ids(),
        )


class GraphSnapshot(BaseModel):
    """The queue and the refinement statuses at one side of a rebuild's persist (spec 6, 8.6)."""

    model_config = ConfigDict(frozen=True)

    pairs: tuple[QueuePair, ...] = ()
    refinements: tuple[RefinementState, ...] = ()

    @property
    def by_key(self) -> dict[tuple[str, str, UnresolvedReason], QueuePair]:
        """The queue under the store's own key, so no two rows for one question collide.

        Not cached: `model_copy(update=...)` rebuilds a frozen model without re-running anything,
        so a cache would go on answering for the pairs the copy replaced.
        """
        return {p.key: p for p in self.pairs}

    @property
    def questions(self) -> frozenset[NodePair]:
        """Every distinct pair the queue still holds, however many rows carry it."""
        return frozenset(p.pair for p in self.pairs)

    @property
    def by_refinement(self) -> dict[int, RefinementState]:
        return {r.refinement_id: r for r in self.refinements}


def _distinct(rows: Iterable[QueuePair]) -> tuple[NodePair, ...]:
    """One pair per question, first seen first: the store keys by reason too, and the gate counts
    questions rather than rows."""
    return tuple({p.pair: None for p in rows})


def new_rows(before: GraphSnapshot, after: GraphSnapshot) -> tuple[QueuePair, ...]:
    """Rows absent before under the whole key, or whose offer moved (spec 8.6).

    Externally bound rows never count, on this side and on the resolved one. Spec 8.6 defines new
    on the offer columns alone, so a row that merely stopped being externally bound is not new
    here even though it has become answerable. The rows rather than the pairs, because the low
    budget narrowing reads a column only a row has.
    """
    was = before.by_key
    return tuple(
        p
        for p in after.pairs
        if not p.externally_bound and (p.key not in was or was[p.key].offer != p.offer)
    )


def new_pairs(before: GraphSnapshot, after: GraphSnapshot) -> tuple[NodePair, ...]:
    """The distinct questions :func:`new_rows` found, which is what the gate's bar counts."""
    return _distinct(new_rows(before, after))


def resolved_pairs(
    before: GraphSnapshot, after: GraphSnapshot, *, removed_nodes: frozenset[str]
) -> tuple[NodePair, ...]:
    """Pairs that disappeared and whose node survived: the resolver settled them (spec 8.6).

    A renamed node produces a removal and a new pair, never a resolution, which is why the removed
    set is subtracted. Externally bound rows are excluded on both sides, as in :func:`new_rows`,
    and a question still carried by any row is not settled.
    """
    still_open = after.questions
    return _distinct(
        p
        for p in before.pairs
        if not p.externally_bound
        and p.pair not in still_open
        and p.node_id not in removed_nodes
    )


def staled_refinements(
    before: GraphSnapshot, after: GraphSnapshot, *, changed_nodes: frozenset[str]
) -> tuple[int, ...]:
    """Refinements this rebuild staled because an anchor this batch touched moved (P10).

    A `noop_builds` or Jaccard staleness lands the same status with no anchor of this batch behind
    it, and an edit cannot re-confirm those, so the intersection is what separates them.
    """
    was = before.by_refinement
    return tuple(
        sorted(
            r.refinement_id
            for r in after.refinements
            if r.status is RefinementStatus.STALE
            and r.refinement_id in was
            and was[r.refinement_id].status is not RefinementStatus.STALE
            and changed_nodes.intersection(r.anchor_nodes)
        )
    )


def _plural(n: int, noun: str) -> str:
    """One count and its noun, so nine reason strings share one pluralizer."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _run_reason(
    *, questions: int, stale: int, new_fired: bool, stale_fired: bool
) -> str:
    """Which clause carried the run, so a reason never credits a bar the count did not clear."""
    if new_fired and stale_fired:
        return f"{_plural(questions, 'new question')} and {_plural(stale, 'stale refinement')}"
    if new_fired:
        return _plural(questions, "new question")
    return _plural(stale, "stale refinement")


def _skip_reason(
    *,
    new: tuple[NodePair, ...],
    bounded: tuple[NodePair, ...],
    stale: tuple[int, ...],
    scheduling: SchedulingConfig,
    narrowed: bool,
) -> str:
    """Why the gate said no, naming the clause that came closest rather than the emptiest one.

    The budget is credited only where it actually removed questions: a narrowing that dropped
    nothing would send a user to wait for a reset when the threshold is the lever.
    """
    if narrowed:
        return (
            f"low budget: {len(bounded)} of {len(new)} new questions are bare or self"
        )
    if new:
        return (
            f"{_plural(len(new), 'new question')}, "
            f"below the {scheduling.min_new_unresolved} the gate needs"
        )
    if stale and not scheduling.run_on_stale:
        return f"{_plural(len(stale), 'stale refinement')}, run_on_stale is off"
    return "no new questions"


def narrowing(
    *,
    new_pairs: tuple[NodePair, ...],
    bounded_pairs: tuple[NodePair, ...],
    budget: BudgetState,
    kind: BatchKind = BatchKind.EDIT,
) -> tuple[tuple[NodePair, ...], bool]:
    """The pairs the gate counts, and whether the low budget rule actually removed any.

    The rule's one home, called once per assessment through :func:`decide`. A suspect or verify
    batch drains capacity already paid for, so it never narrows.
    """
    if kind is not BatchKind.EDIT or not budget.low:
        return new_pairs, False
    return bounded_pairs, len(bounded_pairs) < len(new_pairs)


def decide(
    *,
    new_pairs: tuple[NodePair, ...],
    bounded_pairs: tuple[NodePair, ...],
    stale_refinements: tuple[int, ...],
    scheduling: SchedulingConfig,
    budget: BudgetState,
    kind: BatchKind = BatchKind.EDIT,
) -> tuple[Decision, tuple[NodePair, ...]]:
    """Spec 8.6's decision and the pairs a run would take, empty whenever the gate said no.

    ``bounded_pairs`` is the ``call_form in {bare, self}`` subset, the only shape that can still auto-activate.
    Under the low budget bar it replaces ``new_pairs`` in the count for edit batches alone; returning the counted set leaves the deferral nothing to re-derive.
    """
    if budget.exhausted:
        return (
            Decision(
                decision=AssessmentDecision.SKIP, reason="the day's budget is spent"
            ),
            (),
        )
    edit = kind is BatchKind.EDIT
    if edit and budget.low and not budget.evaluated:
        return (
            Decision(
                decision=AssessmentDecision.SKIP,
                reason="low budget and no eval row for this runner",
            ),
            (),
        )
    counted, narrowed = narrowing(
        new_pairs=new_pairs, bounded_pairs=bounded_pairs, budget=budget, kind=kind
    )
    new_fired = len(counted) >= scheduling.min_new_unresolved
    stale_fired = scheduling.run_on_stale and bool(stale_refinements)
    if new_fired or stale_fired:
        return (
            Decision(
                decision=AssessmentDecision.RUN,
                reason=_run_reason(
                    questions=len(counted),
                    stale=len(stale_refinements),
                    new_fired=new_fired,
                    stale_fired=stale_fired,
                ),
            ),
            counted,
        )
    return (
        Decision(
            decision=AssessmentDecision.SKIP,
            reason=_skip_reason(
                new=new_pairs,
                bounded=bounded_pairs,
                stale=stale_refinements,
                scheduling=scheduling,
                narrowed=narrowed,
            ),
        ),
        (),
    )


def assess_unchanged(stage1: Stage1) -> Assessment:
    """The assessment for a batch stage 1 dropped: no persist, no rebuild, no run (spec 8.6)."""
    return Assessment(
        files=stage1.files,
        verdict=Decision(
            decision=AssessmentDecision.SKIP, reason="no structural change"
        ),
    )


def assess(
    stage1: Stage1,
    *,
    before: GraphSnapshot,
    after: GraphSnapshot,
    scheduling: SchedulingConfig,
    budget: BudgetState,
    max_nodes_per_run: int,
    flow_nodes: frozenset[str] = frozenset(),
) -> Assessment:
    """The whole assessment for an edit batch the loop rebuilt for (spec 8.6 stages 1 and 2).

    ``before`` and ``after`` are the snapshots the rebuild took around its one persist commit;
    ``flow_nodes`` is what a recent flow query asked about, which is recorded and decides nothing.
    A suspect or verify batch gates through :func:`decide` directly, with its own ``kind``.
    """
    rows = new_rows(before, after)
    fresh = _distinct(rows)
    bounded = _distinct(p for p in rows if p.call_form in _BOUNDED_FORMS)
    staled = staled_refinements(before, after, changed_nodes=stage1.changed_nodes)
    verdict, targets = decide(
        new_pairs=fresh,
        bounded_pairs=bounded,
        stale_refinements=staled,
        scheduling=scheduling,
        budget=budget,
    )
    return Assessment(
        files=stage1.files,
        added_nodes=stage1.added_nodes,
        removed_nodes=stage1.removed_nodes,
        facts_changed_nodes=stage1.facts_changed_nodes,
        new_pairs=fresh,
        resolved_pairs=resolved_pairs(
            before, after, removed_nodes=frozenset(stage1.removed_nodes)
        ),
        stale_refinements=staled,
        affected_flow=tuple(sorted(stage1.changed_nodes & flow_nodes)),
        deferred_pairs=max(0, len(targets) - max_nodes_per_run),
        verdict=verdict,
    )
