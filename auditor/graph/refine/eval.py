"""The numbers that decide whether a runner may activate structural corrections (spec 10).

Masks known-true edges of this repo's own deterministic graph, presents them to a runner as
unresolved rows, and judges every proposal against the ground truth. Nothing here writes a graph
table, and no proposal reaches the ledger: the judge answers `propose` and the run commits empty.
"""

import logging
import random
from abc import abstractmethod
from collections import Counter
from collections.abc import Callable, Container, Mapping, Sequence
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auditor.graph.model import (
    TEST_ROLES,
    EdgeKind,
    FactKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    UnresolvedReason,
    UnresolvedRow,
)
from auditor.graph.refine.facts import FactReader
from auditor.graph.refine.models import (
    BOUNDED_FORMS,
    PRECISION_SUITES,
    CallForm,
    ClientKind,
    EvalSuite,
    ProducerKind,
    Proposal,
    ProposalOutcome,
    Proposer,
    RefinementKind,
    RefinementStatus,
    RefusalKind,
    Run,
    RunnerKind,
    RunStatus,
    Stratum,
    SuiteSpend,
    SuiteTally,
    Tier,
    TriggerKind,
    Verdict,
    flawless_floor,
    key_of,
)
from auditor.graph.refine.namespace import file_of, short_name
from auditor.graph.refine.payloads import (
    EvalActivation,
    EvalNotes,
    EvalPlan,
    EvalReport,
)
from auditor.graph.refine.runner import RefinementJob, RefinementRunner
from auditor.graph.refine.service import RefinementRefused, RefinementService
from auditor.graph.refine.tiers import TierPolicy
from auditor.graph.refine.verify import VerifyStatus
from auditor.graph.resolve_edges import (
    NameBindings,
    call_forms,
    form_for,
    resolve_structural,
)
from auditor.user_settings import ClaudeModel

logger = logging.getLogger(__name__)

#: how many decoys a decoy trial offers beside the true destination (spec 10.2)
DECOY_COUNT = 3

#: the key an off-target proposal is recorded under: it belongs to no trial and scores for none
OFF_TARGET = ("", "")

#: what the judge answers a model with, so the run continues exactly as a real one would
JUDGED = "recorded by the eval"

#: the kinds that add or pick an edge, so one aimed at nothing the batch asked is a false add
SCORING_KINDS = (RefinementKind.ADD_EDGE, RefinementKind.RESOLVE_AMBIGUOUS)

#: how one batch's runner is built: the service holding its masked queue, and its judge
RunnerFactory = Callable[[RefinementService, Proposer], RefinementRunner]

#: what a stratum that did not finish its runs leaves behind, said once however it stopped
NO_ROW = "no row is written and the last complete measurement stands"


class ExclusionRule(StrEnum):
    """Why a resolved `calls` edge is not one of the add suite's truths (spec 10.2)."""

    #: the site is an attribute call, or the node binds the name itself, so no tier B row exists
    NOT_BOUNDED_FORM = "not-bounded-form"
    #: the role-filtered definers a real queue row would carry are not this destination alone
    NOT_SOLE_DEFINER = "not-sole-definer"
    #: the caller's module binds the name from a non-repo import, so the queue's row is tier C
    EXTERNALLY_BOUND = "externally-bound"


class Truth(BaseModel):
    """One resolved edge the add suite can mask, with everything its queue row would carry."""

    model_config = ConfigDict(frozen=True)

    src: str
    dst: str
    name: str
    edge_kind: EdgeKind
    call_form: CallForm
    receiver_root: str | None = None
    stratum: Stratum


def truth_of(
    edge: GraphEdge,
    node: GraphNode,
    definers: Mapping[str, Sequence[str]],
    bindings: NameBindings,
) -> Truth | ExclusionRule:
    """Spec 10.2's tier B shape for one resolved `calls` edge, or the rule that leaves it out.

    One decision point for both answers, and the three rules are the three tier B conditions
    `TierPolicy.tier` reads, each applied through the call the queue writer itself makes.
    """
    name = short_name(edge.dst)
    found = form_for(call_forms(node), name, node.local_names)
    if found is None or found[0] not in BOUNDED_FORMS:
        return ExclusionRule.NOT_BOUNDED_FORM
    if tuple(definers.get(name, ())) != (edge.dst,):
        return ExclusionRule.NOT_SOLE_DEFINER
    call_form, receivers = found
    receiver_root = receivers[0] if receivers else None
    # `UnresolvedCollector._row`'s own call: the called name as well as the receiver it was on
    if bindings.externally_bound(node.module, name, receiver_root):
        return ExclusionRule.EXTERNALLY_BOUND
    return Truth(
        src=edge.src,
        dst=edge.dst,
        name=name,
        edge_kind=EdgeKind.CALLS,
        call_form=call_form,
        receiver_root=receiver_root,
        stratum=Stratum.of(
            edge.src,
            edge.dst,
            imports=bindings.imported_module_ids(file_of(edge.src)),
        ),
    )


class Trial(BaseModel):
    """One masked row a runner is asked to answer, and the answer it is judged against."""

    model_config = ConfigDict(frozen=True)

    suite: EvalSuite
    stratum: Stratum
    row: UnresolvedRow
    #: the masked destination for `add` and `decoy`; a control has no right answer to name
    truth: str | None = None
    edge_kind: EdgeKind | None = None

    @property
    def key(self) -> tuple[str, str]:
        """What a proposal is matched to its trial by."""
        return (self.row.node_id, self.row.name)


class Judgement(BaseModel):
    """One trial, what the runner earned on it, and what the judge turned away."""

    model_config = ConfigDict(frozen=True)

    trial: Trial
    verdict: str
    #: the validator's complaint about each proposal the judge refused, which scores for nothing
    refusals: tuple[str, ...] = ()


class Ground(BaseModel):
    """Every resolved `calls` edge of one checkout: the truths, and what excluded the rest.

    Kept whole rather than filtered on the way in, so each ground-truth rule is answerable by the
    sites it removes rather than only by how many truths are left.
    """

    model_config = ConfigDict(frozen=True)

    #: ``(src, dst)`` -> the truth it makes, or the one rule that leaves it out
    sites: Mapping[tuple[str, str], Truth | ExclusionRule] = Field(default_factory=dict)

    @classmethod
    def of(
        cls,
        nodes: Sequence[GraphNode],
        definers: Mapping[str, Sequence[str]],
        bindings: NameBindings,
    ) -> "Ground":
        """Judge every resolved `calls` edge this checkout holds."""
        by_id = {node.id: node for node in nodes}
        found: dict[tuple[str, str], Truth | ExclusionRule] = {}
        for edge in resolve_structural(nodes).edges:
            node = by_id.get(edge.src)
            if edge.kind is EdgeKind.CALLS and node is not None:
                found[(edge.src, edge.dst)] = truth_of(edge, node, definers, bindings)
        return cls(sites=found)

    @property
    def truths(self) -> tuple[Truth, ...]:
        return tuple(found for found in self.sites.values() if isinstance(found, Truth))

    def excluded_by(self, rule: ExclusionRule) -> tuple[tuple[str, str], ...]:
        """The edges one rule alone leaves out, which is what holds that rule to its own cases."""
        return tuple(
            sorted(site for site, found in self.sites.items() if found is rule)
        )


class Population(BaseModel):
    """What one checkout offers each suite: the truths, the collisions, the decoys, the names."""

    model_config = ConfigDict(frozen=True)

    ground: Ground = Ground()
    collisions: tuple[UnresolvedRow, ...] = ()
    #: short name -> every node that carries it, which is where a decoy trial finds its decoys
    decoy_pool: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    names_defined: frozenset[str] = frozenset()
    #: symbol ids a negative trial can hang a defined-nowhere name on
    sources: tuple[str, ...] = ()
    #: node id -> its kind, so a class is never offered as a decoy for a function
    kinds: Mapping[str, str] = Field(default_factory=dict)

    @classmethod
    async def of(cls, facts: FactReader) -> "Population":
        """Read the graph, resolve it again, and keep the edges tier B is measured on (spec 10.2).

        `facts.files` re-extracts every file from disk, which is the one expensive read here and
        runs once per eval invocation.
        """
        graph = facts.index.graph
        rows = await graph.nodes()
        by_file, _missing = await facts.files(
            sorted({row["module"] for row in rows if row.get("module")})
        )
        nodes = [node for file in by_file.values() for node in file.nodes]
        bindings = NameBindings.of(
            [node for node in nodes if node.kind is NodeKind.MODULE],
            module_ids=await graph.module_ids(),
        )
        definers: dict[str, list[str]] = {}
        named: dict[str, list[str]] = {}
        for row in rows:
            named.setdefault(row["name"], []).append(row["node_id"])
            if row["role"] not in TEST_ROLES:
                definers.setdefault(row["name"], []).append(row["node_id"])
        queue = await facts.queue(None, limit=None, external=True)
        return cls(
            ground=Ground.of(nodes, definers, bindings),
            collisions=tuple(row for row in queue if row.externally_bound),
            # every node of the name, not the role-filtered definers: a truth has exactly one of
            # those by construction, so a pool built from them would offer no decoy at all
            decoy_pool={
                name: tuple(ids) for name, ids in named.items() if len(ids) > 1
            },
            names_defined=frozenset(definers),
            sources=tuple(sorted(node.id for node in nodes if "::" in node.id)),
            kinds={row["node_id"]: str(row["kind"]) for row in rows},
        )

    @property
    def truths(self) -> tuple[Truth, ...]:
        """The edges the add suite can mask, which is the ground truth minus every exclusion."""
        return self.ground.truths

    def sample(self, *, suite: EvalSuite, size: int, seed: int) -> tuple[Trial, ...]:
        """Draw at most ``size`` trials per stratum, deterministically under ``seed`` (spec 10.2).

        A suite stored under one stratum has exactly one, so it draws ``min(size, available)``.
        """
        return EvalSuiteSpec.of(suite).draw(self, random.Random(seed), size)

    def adds(self, rng: random.Random, size: int) -> tuple[Trial, ...]:
        """The add suite: each stratum shuffled and capped alone, so one run holds one stratum."""
        out: list[Trial] = []
        for stratum in Stratum.add_strata():
            truths = [truth for truth in self.truths if truth.stratum is stratum]
            rng.shuffle(truths)
            out.extend(_masked(truth) for truth in truths[:size])
        return tuple(out)

    def decoys(self, rng: random.Random, size: int) -> tuple[Trial, ...]:
        """The decoy suite: the same truths, offered as candidates the runner chooses between.

        Distractors are drawn without replacement across the draw while the pool lasts, so the
        true destination is not the one candidate that changes from trial to trial.
        """
        truths = list(self.truths)
        rng.shuffle(truths)
        used: set[str] = set()
        out: list[Trial] = []
        for truth in truths[:size]:
            decoys = self.decoys_for(truth, rng, used=used)
            used.update(decoys)
            candidates = [truth.dst, *decoys]
            rng.shuffle(candidates)
            out.append(
                Trial(
                    suite=EvalSuite.DECOY,
                    stratum=Stratum.ALL,
                    row=UnresolvedRow(
                        node_id=truth.src,
                        fact_kind=FactKind.CALLEE,
                        name=truth.name,
                        reason=UnresolvedReason.AMBIGUOUS_NAME,
                        call_form=truth.call_form,
                        receiver_root=truth.receiver_root,
                        # empty, so a row about choosing never offers `add_edge` (spec 9.2)
                        definers=(),
                        candidates=tuple(candidates),
                    ),
                    truth=truth.dst,
                    edge_kind=truth.edge_kind,
                )
            )
        return tuple(out)

    def decoys_for(
        self, truth: Truth, rng: random.Random, *, used: Container[str] = frozenset()
    ) -> list[str]:
        """Up to three wrong answers for one truth, drawn with ``rng``.

        A node in ``used`` is a last resort, so the trials of one batch do not share distractors
        while the pool lasts; inside that the destination's own kind comes first, and inside that
        the same short name, then the destination's own module, then the rest of the graph.
        """
        want = self.kinds.get(truth.dst)
        same_kind = [
            [node for node in pool if self.kinds.get(node) == want]
            for pool in self._decoy_pools(truth)
        ]
        pools = (*same_kind, *self._decoy_pools(truth))
        out: list[str] = []
        for fresh in (True, False):
            for pool in pools:
                picks = [
                    node
                    for node in pool
                    if node not in out and not (fresh and node in used)
                ]
                rng.shuffle(picks)
                out.extend(picks[: DECOY_COUNT - len(out)])
                if len(out) == DECOY_COUNT:
                    return out
        return out

    def _decoy_pools(self, truth: Truth) -> tuple[list[str], ...]:
        """The candidate pools one truth draws decoys from, best first."""
        module = file_of(truth.dst)
        same_name = [
            node for node in self.decoy_pool.get(truth.name, ()) if node != truth.dst
        ]
        same_module = [
            node
            for node in self.sources
            if node != truth.dst and file_of(node) == module
        ]
        rest = [node for node in self.sources if node != truth.dst]
        return (same_name, same_module, rest)

    def collision_trials(self, rng: random.Random, size: int) -> tuple[Trial, ...]:
        """The collision suite: the externally bound queue rows this checkout already holds."""
        rows = list(self.collisions)
        rng.shuffle(rows)
        return tuple(_control(EvalSuite.COLLISION, row) for row in rows[:size])

    def negatives(self, rng: random.Random, size: int) -> tuple[Trial, ...]:
        """The negative suite: names this repo defines nowhere, asked about at real source nodes."""
        sources = list(self.sources)
        rng.shuffle(sources)
        out: list[Trial] = []
        for index, node_id in enumerate(sources):
            if len(out) == size:
                break
            name = f"{short_name(node_id)}_missing{index}"
            if name in self.names_defined:
                continue
            out.append(
                _control(
                    EvalSuite.NEGATIVE,
                    UnresolvedRow(
                        node_id=node_id,
                        fact_kind=FactKind.CALLEE,
                        name=name,
                        reason=UnresolvedReason.UNIMPORTABLE_NAME,
                        call_form=CallForm.BARE,
                    ),
                )
            )
        return tuple(out)


def _masked(truth: Truth) -> Trial:
    """One add trial: the row the queue would have written, with the edge taken away."""
    return Trial(
        suite=EvalSuite.ADD,
        stratum=truth.stratum,
        row=UnresolvedRow(
            node_id=truth.src,
            fact_kind=FactKind.CALLEE,
            name=truth.name,
            reason=UnresolvedReason.UNIMPORTABLE_NAME,
            call_form=truth.call_form,
            receiver_root=truth.receiver_root,
            definers=(truth.dst,),
            # every truth cleared the externally-bound rule, so the queue's own value is False
            externally_bound=False,
        ),
        truth=truth.dst,
        edge_kind=truth.edge_kind,
    )


def _control(suite: EvalSuite, row: UnresolvedRow) -> Trial:
    """One control trial: no right answer to name, so any add is a false add."""
    return Trial(suite=suite, stratum=Stratum.ALL, row=row)


#: every suite this build can draw, filled by `EvalSuiteSpec.__init_subclass__`
_SPECS: dict[EvalSuite, "EvalSuiteSpec"] = {}


class EvalSuiteSpec(BaseModel):
    """One suite's draw, its verdict rule and the strata it stores rows under (spec 10.2).

    A suite is one subclass registered by its own definition, so landing the fixtures suite is a
    class here rather than an edit at every site that switches on `EvalSuite`.
    """

    model_config = ConfigDict(frozen=True)

    SUITE: ClassVar[EvalSuite]
    STRATA: ClassVar[tuple[Stratum, ...]] = (Stratum.ALL,)

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        _SPECS[cls.SUITE] = cls()

    @staticmethod
    def of(suite: EvalSuite) -> "EvalSuiteSpec":
        """The spec one suite draws and scores through.

        Raises:
            ValueError: the suite is in the vocabulary but this build draws nothing for it, which
                is a mis-dispatch rather than a reason to measure a different suite.
        """
        spec = _SPECS.get(suite)
        if spec is None:
            raise ValueError(f"{suite.value} has no draw in this build")
        return spec

    @property
    def precision_gated(self) -> bool:
        """Whether a Wilson bound gates this suite, rather than having produced no false add."""
        return self.SUITE in PRECISION_SUITES

    @abstractmethod
    def draw(
        self, population: Population, rng: random.Random, size: int
    ) -> tuple[Trial, ...]:
        """This suite's trials, at most ``size`` per stratum."""

    @abstractmethod
    def verdict(self, trial: Trial, proposed: Sequence[Proposal]) -> str:
        """What one trial's staged proposals earned, per spec 10.2's five outcomes."""


class AddSuite(EvalSuiteSpec):
    """Masked true edges, judged on whether the runner names the destination back."""

    SUITE: ClassVar[EvalSuite] = EvalSuite.ADD
    STRATA: ClassVar[tuple[Stratum, ...]] = Stratum.add_strata()

    def draw(
        self, population: Population, rng: random.Random, size: int
    ) -> tuple[Trial, ...]:
        return population.adds(rng, size)

    def verdict(self, trial: Trial, proposed: Sequence[Proposal]) -> str:
        added = [p for p in proposed if p.kind is RefinementKind.ADD_EDGE]
        if not added:
            return "missed"
        if (
            len(added) == 1
            and added[0].target.dst == trial.truth
            and added[0].target.edge_kind is trial.edge_kind
        ):
            return "correct"
        return "wrong"


class DecoySuite(EvalSuiteSpec):
    """The same truths offered as candidates, judged on which one the runner picks."""

    SUITE: ClassVar[EvalSuite] = EvalSuite.DECOY

    def draw(
        self, population: Population, rng: random.Random, size: int
    ) -> tuple[Trial, ...]:
        return population.decoys(rng, size)

    def verdict(self, trial: Trial, proposed: Sequence[Proposal]) -> str:
        chosen = [p for p in proposed if p.kind is RefinementKind.RESOLVE_AMBIGUOUS]
        added = [p for p in proposed if p.kind is RefinementKind.ADD_EDGE]
        if len(chosen) == 1 and chosen[0].payload.candidate == trial.truth:
            return "correct"
        # an `add_edge` the brief never offered is a wrong answer, not a missing one
        return "wrong" if chosen or added else "missed"


class CollisionSuite(EvalSuiteSpec):
    """Externally bound queue rows, where the only right answer is to add nothing."""

    SUITE: ClassVar[EvalSuite] = EvalSuite.COLLISION

    def draw(
        self, population: Population, rng: random.Random, size: int
    ) -> tuple[Trial, ...]:
        return population.collision_trials(rng, size)

    def verdict(self, trial: Trial, proposed: Sequence[Proposal]) -> str:
        return (
            "false_add"
            if any(p.kind is RefinementKind.ADD_EDGE for p in proposed)
            else "clean"
        )


class NegativeSuite(EvalSuiteSpec):
    """Names this repo defines nowhere, where any edge at all is a false add."""

    SUITE: ClassVar[EvalSuite] = EvalSuite.NEGATIVE

    def draw(
        self, population: Population, rng: random.Random, size: int
    ) -> tuple[Trial, ...]:
        return population.negatives(rng, size)

    def verdict(self, trial: Trial, proposed: Sequence[Proposal]) -> str:
        return (
            "false_add" if any(p.kind in SCORING_KINDS for p in proposed) else "clean"
        )


def batches(trials: Sequence[Trial], size: int) -> tuple[tuple[Trial, ...], ...]:
    """Group trials into runs of at most ``size``, never mixing strata (spec 10.2).

    A run then belongs to exactly one `graph_evals` row, so its cost and turns need no
    apportioning.
    """
    out: list[tuple[Trial, ...]] = []
    for stratum in dict.fromkeys(trial.stratum for trial in trials):
        group = [trial for trial in trials if trial.stratum is stratum]
        out.extend(
            tuple(group[start : start + size]) for start in range(0, len(group), size)
        )
    return tuple(out)


class Judge(BaseModel):
    """Scores one batch's proposals against its trials, and stores none of them (spec 10.2).

    A mutable aggregate like `BoundTools`: filled in as the run proceeds, read when it ends. Only
    what a real service would have staged is scored, so a payload its validators refuse earns
    nothing rather than counting as an answer.
    """

    model_config = ConfigDict(frozen=False)

    trials: Mapping[tuple[str, str], Trial]
    staged: dict[tuple[str, str], list[Proposal]] = Field(default_factory=dict)
    #: the complaint about each refused payload, under the same key its target named
    refused: dict[tuple[str, str], list[str]] = Field(default_factory=dict)

    @classmethod
    def over(cls, trials: Sequence[Trial]) -> "Judge":
        return cls(trials={trial.key: trial for trial in trials})

    async def propose(self, _run_id: str, raw: Mapping[str, object]) -> Verdict:
        """Record one proposal and answer as the service would, storing no row.

        Raises:
            RefinementRefused: the payload has no readable ``kind``, the one thing no lenient read
                can rescue and no verdict can be returned for.
        """
        try:
            proposal, complaint = Proposal.read(raw)
        except ValidationError as exc:
            raise RefinementRefused.not_a_proposal(exc) from exc
        target = proposal.target
        named = (target.node_id or target.src or "", target.name or "")
        key = named if named in self.trials else OFF_TARGET
        if complaint:
            self.refused.setdefault(key, []).append(complaint)
            return Verdict(
                outcome=ProposalOutcome.REJECTED,
                kind=proposal.kind,
                refusal=RefusalKind.INVALID,
                detail=complaint,
            )
        self.staged.setdefault(key, []).append(proposal)
        return Verdict(
            outcome=ProposalOutcome.STAGED,
            kind=proposal.kind,
            tier=Tier.C,
            status=RefinementStatus.PENDING,
            verify=VerifyStatus.UNVERIFIED,
            detail=JUDGED,
        )

    @property
    def off_target(self) -> tuple[Proposal, ...]:
        """Proposals about a node and name no trial in this batch asked about."""
        return tuple(self.staged.get(OFF_TARGET, ()))

    def judgements(self) -> tuple[Judgement, ...]:
        """One judgement per trial, whatever the runner did or did not propose."""
        return tuple(
            Judgement(
                trial=trial,
                verdict=EvalSuiteSpec.of(trial.suite).verdict(
                    trial, self.staged.get(key, ())
                ),
                refusals=tuple(self.refused.get(key, ())),
            )
            for key, trial in self.trials.items()
        )


def off_target_note(key: str, proposal: Proposal) -> str:
    """One report line for a proposal no trial asked about, which a real run would have refused."""
    target = proposal.target
    where = target.node_id or target.src or "(no node)"
    return (
        f"{key}: {proposal.kind.value} about {where} ({target.name or 'no name'}), "
        "which no trial asked about"
    )


def tally(
    judgements: Sequence[Judgement],
    *,
    suite: EvalSuite,
    spend: Mapping[Stratum, SuiteSpend],
    off_target: Mapping[Stratum, Sequence[Proposal]],
) -> tuple[SuiteTally, ...]:
    """Sum one suite's judgements per stratum, with the cost and turns its runs spent.

    ``spend`` is read off the closed run rows: an opened row's usage is still empty and summing
    that would report every eval as free. A scored off-target proposal lands where the suite's own
    gate reads it: `wrong` for a precision suite, so the Wilson bound carries it, else `false_adds`.
    """
    precision = EvalSuiteSpec.of(suite).precision_gated
    grouped: dict[Stratum, list[Judgement]] = {}
    for judgement in judgements:
        grouped.setdefault(judgement.trial.stratum, []).append(judgement)
    out: list[SuiteTally] = []
    for stratum, group in grouped.items():
        counts = Counter(judgement.verdict for judgement in group)
        stray = tuple(off_target.get(stratum, ()))
        scored = sum(1 for p in stray if p.kind in SCORING_KINDS)
        out.append(
            SuiteTally(
                suite=suite.value,
                stratum=stratum,
                n=len(group),
                correct=counts["correct"],
                wrong=counts["wrong"] + (scored if precision else 0),
                missed=counts["missed"],
                false_adds=counts["false_add"] + (0 if precision else scored),
                off_target=len(stray),
                spend=spend.get(stratum, SuiteSpend()),
            )
        )
    return tuple(out)


class SuiteResult(BaseModel):
    """One suite's measured strata, and the sentences the report says about what it could not."""

    model_config = ConfigDict(frozen=True)

    tallies: tuple[SuiteTally, ...] = ()
    #: strata no row was written for, each with the reason it went unmeasured
    stopped: tuple[str, ...] = ()
    #: proposals no trial asked about, one line each
    off_target: tuple[str, ...] = ()
    #: what every run of this suite cost, the one that stopped it included: money is spent whether
    #: or not its trials could be judged, and the total is what the operator is owed
    spent: float = 0.0


class EvalRun(BaseModel):
    """One `auditr graph eval` invocation: what it measures, and what it has spent so far.

    A mutable aggregate like `Judge`: the running spend is the one thing that crosses suites, and
    the eval ceiling is read against it before each run opens.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=False)

    service: RefinementService
    build: RunnerFactory
    runner: RunnerKind
    model: ClaudeModel | None
    size: int
    seed: int
    #: called once with the plan before the first run opens, so an operator can still stop
    on_plan: Callable[[EvalPlan], None] | None = None
    spent: float = 0.0

    @property
    def min_precision(self) -> float:
        return self.service.user.observer.tuning.min_precision

    @property
    def ceiling(self) -> float:
        """What this whole invocation may spend before it stops opening runs."""
        return self.service.user.observer.budget.max_budget_usd_per_eval

    @property
    def per_run(self) -> float:
        """What one more run may cost, which is the ceiling handed to it."""
        return self.service.user.observer.budget.max_budget_usd_per_run

    async def report(
        self, suites: Sequence[EvalSuite], *, dry_run: bool = False
    ) -> EvalReport:
        """Measure ``suites`` on this checkout and store one `graph_evals` row per stratum.

        ``dry_run`` answers with the plan alone and opens no run, so the cost is readable before
        any of it is spent. A stratum whose runs did not all complete writes no row: an abort is
        not a measurement, and the row it would overwrite was one.
        """
        population = await Population.of(self.service.facts)
        drawn = {
            suite: population.sample(suite=suite, size=self.size, seed=self.seed)
            for suite in suites
        }
        groups = {
            suite: batches(trials, self.service.user.observer.limits.max_nodes_per_run)
            for suite, trials in drawn.items()
        }
        plan = self._plan(drawn, groups)
        if self.on_plan is not None:
            self.on_plan(plan)
        notes = _draw_notes(drawn, self.size, self.min_precision)
        if dry_run:
            return self._empty_report(plan, notes)
        results = [
            await self._measure(suite, batched) for suite, batched in groups.items()
        ]
        tallies = tuple(got for result in results for got in result.tallies)
        await self._store(tallies)
        policy = TierPolicy.of(
            await self.service.index.evals.latest(self.runner, self.model or ""),
            min_precision=self.min_precision,
            runner=self.runner,
            model=self.model or "",
        )
        return EvalReport(
            runner=self.runner,
            model=self.model or "",
            min_precision=self.min_precision,
            plan=plan,
            suites=tallies,
            notes=notes.model_copy(
                update={
                    "stopped": tuple(
                        line for result in results for line in result.stopped
                    ),
                    "off_target": tuple(
                        line for result in results for line in result.off_target
                    ),
                    "unprovable_judged": _unprovable_judged(
                        tallies, self.min_precision
                    ),
                }
            ),
            activation=_activation(policy),
            cost_usd=sum(result.spent for result in results),
            runs=sum(got.spend.runs for got in tallies),
        )

    def _empty_report(self, plan: EvalPlan, notes: EvalNotes) -> EvalReport:
        """The answer a `--dry-run` gives: the plan and the draw, with nothing measured."""
        return EvalReport(
            runner=self.runner,
            model=self.model or "",
            min_precision=self.min_precision,
            plan=plan,
            notes=notes,
        )

    def _plan(
        self,
        drawn: Mapping[EvalSuite, Sequence[Trial]],
        groups: Mapping[EvalSuite, Sequence[Sequence[Trial]]],
    ) -> EvalPlan:
        """What this invocation will draw and what it may spend, before a run opens."""
        budget = self.service.user.observer.budget
        return EvalPlan(
            sample=self.size,
            seed=self.seed,
            suites=tuple(suite.value for suite in drawn),
            strata=tuple(
                f"{key_of(suite.value, stratum)}: {drew} trials"
                for suite, trials in drawn.items()
                for stratum in EvalSuiteSpec.of(suite).STRATA
                if (drew := _drawn_in(trials, stratum))
            ),
            runs_planned=sum(len(batched) for batched in groups.values()),
            max_budget_usd_per_run=budget.max_budget_usd_per_run,
            max_budget_usd_per_eval=budget.max_budget_usd_per_eval,
        )

    async def _store(self, tallies: Sequence[SuiteTally]) -> None:
        """Write one `graph_evals` row per measured stratum, which is what the gate reads back."""
        identity = self.service.identity
        for got in tallies:
            await self.service.index.evals.add_eval(
                got.row(identity=identity, runner=self.runner, model=self.model or "")
            )

    async def _measure(
        self, suite: EvalSuite, groups: Sequence[Sequence[Trial]]
    ) -> SuiteResult:
        """Drive one suite's batches, stopping at the first run that produced no measurement."""
        planned = Counter(group[0].stratum for group in groups)
        done: Counter[Stratum] = Counter()
        judged: list[Judgement] = []
        spend: dict[Stratum, SuiteSpend] = {}
        stray: dict[Stratum, list[Proposal]] = {}
        stopped: dict[Stratum, str] = {}
        notes: list[str] = []
        spent = 0.0
        for group in groups:
            stratum = group[0].stratum
            key = key_of(suite.value, stratum)
            if self.spent + self.per_run > self.ceiling:
                stopped[stratum] = (
                    f"stopped: budget, ${self.spent:.4f} spent of the "
                    f"${self.ceiling:.2f} eval ceiling"
                )
                break
            judge = Judge.over(group)
            row, unmeasured = await self._one_batch(group, judge)
            if row is not None:
                spent += row.usage.cost_usd
                self.spent += row.usage.cost_usd
            if unmeasured:
                stopped[stratum] = unmeasured
                break
            if row is None or row.status is not RunStatus.SUCCEEDED:
                status = row.status.value if row is not None else "unopened"
                cost = row.usage.cost_usd if row is not None else 0.0
                stopped[stratum] = f"a run ended {status} after ${cost:.4f}"
                break
            judged.extend(judge.judgements())
            done[stratum] += 1
            spend[stratum] = spend.get(stratum, SuiteSpend()).plus(row.usage)
            stray.setdefault(stratum, []).extend(judge.off_target)
            notes.extend(off_target_note(key, p) for p in judge.off_target)
        complete = {
            stratum for stratum, runs in planned.items() if done[stratum] == runs
        }
        for stratum, runs in planned.items():
            if stratum not in complete:
                stopped.setdefault(stratum, f"{done[stratum]} of {runs} runs completed")
        return SuiteResult(
            tallies=tuple(
                got
                for got in tally(judged, suite=suite, spend=spend, off_target=stray)
                if got.stratum in complete
            ),
            stopped=tuple(
                f"{key_of(suite.value, stratum)}: {why}; {NO_ROW}"
                for stratum, why in stopped.items()
            ),
            off_target=tuple(notes),
            spent=spent,
        )

    async def _one_batch(
        self, trials: Sequence[Trial], judge: Judge
    ) -> tuple[Run | None, str]:
        """One run over one batch's masked queue, its closed row, and why it measured nothing.

        A run whose brief did not carry every trial is not a measurement: the model was never
        asked, so a control could otherwise clear its gate over questions nobody put to it.
        """
        masked = RefinementService(
            self.service.index,
            self.service.root,
            self.service.settings,
            self.service.user,
            facts=self.service.facts.model_copy(
                update={"synthetic": tuple(trial.row for trial in trials)}
            ),
        )
        try:
            product = await self.build(masked, judge.propose).run(
                RefinementJob(
                    scope="",
                    trigger=TriggerKind.EVAL,
                    producer=ProducerKind.CLI,
                    client=ClientKind.CLI,
                    model=self.model,
                )
            )
        except RefinementRefused as exc:
            logger.warning("eval batch refused: %s", exc)
            return None, f"the run was refused ({exc}), so its trials are not counted"
        row = await self.service.index.runs.run(product.run.run_id)
        briefed = len(product.brief.targets)
        if briefed != len(trials):
            status = row.status.value if row is not None else "unopened"
            return row, (
                f"the run ended {status} unbriefed, {briefed} of {len(trials)} trials "
                "reached its brief, so nothing it did is a measurement"
            )
        return row, ""


def _activation(policy: TierPolicy) -> EvalActivation:
    """What the gate now lets through, asked of the policy the ledger itself reads."""
    return EvalActivation(
        proven=tuple(sorted(key_of(*pair) for pair in policy.proven)),
        tier_b=tuple(
            stratum.value
            for stratum in Stratum.add_strata()
            if policy.status(RefinementKind.ADD_EDGE, Tier.B, stratum=stratum)
            is RefinementStatus.ACTIVE
        ),
        resolve_ambiguous=policy.status(RefinementKind.RESOLVE_AMBIGUOUS, Tier.A)
        is RefinementStatus.ACTIVE,
    )


def _drawn_in(trials: Sequence[Trial], stratum: Stratum) -> int:
    return sum(1 for trial in trials if trial.stratum is stratum)


def _draw_notes(
    drawn: Mapping[EvalSuite, Sequence[Trial]], size: int, min_precision: float
) -> EvalNotes:
    """What the draw alone already says, which is everything a `--dry-run` can answer."""
    empty: list[str] = []
    short: list[str] = []
    unprovable: list[str] = []
    floor = flawless_floor(min_precision)
    for suite, trials in drawn.items():
        spec = EvalSuiteSpec.of(suite)
        for stratum in spec.STRATA:
            key = key_of(suite.value, stratum)
            drew = _drawn_in(trials, stratum)
            if drew == 0:
                empty.append(key)
                continue
            if drew < size:
                short.append(f"{key}: drew {drew} of {size}")
            if spec.precision_gated:
                unprovable.extend(_below_floor(key, drew, floor, min_precision))
    return EvalNotes(
        short=tuple(short), empty=tuple(empty), unprovable_drawn=tuple(unprovable)
    )


def _unprovable_judged(
    tallies: Sequence[SuiteTally], min_precision: float
) -> tuple[str, ...]:
    """The strata whose judged trials cannot clear ``min_precision`` however flawless they were.

    A full draw can still be judged on too few trials: a Wilson bound reads ``correct + wrong``,
    not ``n``, so a stratum the runner mostly ignored is unprovable at any draw size.
    """
    floor = flawless_floor(min_precision)
    out: list[str] = []
    for got in tallies:
        if not EvalSuiteSpec.of(EvalSuite(got.suite)).precision_gated:
            continue
        key = key_of(got.suite, got.stratum)
        out.extend(
            _below_floor(key, got.correct + got.wrong, floor, min_precision, "judged ")
        )
    return tuple(out)


def _below_floor(
    key: str, count: int, floor: int | None, min_precision: float, what: str = ""
) -> tuple[str, ...]:
    """One sentence when ``count`` cannot clear the bar, or nothing when it can."""
    if floor is None:
        return (f"{key}: no run of any size clears {min_precision}",)
    if count < floor:
        return (
            f"{key}: {what}{count} trials, below the {floor} a flawless run needs "
            f"at {min_precision}",
        )
    return ()
