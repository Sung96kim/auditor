"""The numbers that decide whether a runner may activate structural corrections (spec 10).

Masks known-true edges of this repo's own deterministic graph, presents them to a runner as
unresolved rows, and judges every proposal against the ground truth. Nothing here writes a graph
table, and no proposal reaches the ledger: the judge answers `propose` and the run commits empty.
"""

import logging
import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auditor.graph.model import (
    TEST_ROLES,
    CallForm,
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
    CONTROL_STRATUM,
    ClientKind,
    EvalStratum,
    EvalSuite,
    ProducerKind,
    Proposal,
    ProposalOutcome,
    Proposer,
    RefinementKind,
    RefinementStatus,
    Run,
    RunnerKind,
    RunStatus,
    RunUsage,
    Stratum,
    SuiteTally,
    Tier,
    TriggerKind,
    Verdict,
    flawless_floor,
)
from auditor.graph.refine.namespace import file_of, short_name
from auditor.graph.refine.payloads import EvalReport
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

#: the two call forms a tier B proposal can be made of, which is what the add suite draws from
BOUNDED_FORMS = (CallForm.BARE, CallForm.SELF)

#: how many decoys a decoy trial offers beside the true destination (spec 10.2)
DECOY_COUNT = 3

#: the suites `--suite all` means; `fixtures` is a follow-up and is refused by name
ALL_SUITES = (EvalSuite.ADD, EvalSuite.COLLISION, EvalSuite.NEGATIVE, EvalSuite.DECOY)

#: the suites a Wilson bound gates, which are the only ones a flawless floor can rule out
PRECISION_SUITES = (EvalSuite.ADD, EvalSuite.DECOY)

#: the key an off-target proposal is recorded under: it belongs to no trial and scores for none
OFF_TARGET = ("", "")

#: what the judge answers a model with, so the run continues exactly as a real one would
JUDGED = "recorded by the eval"

#: how one batch's runner is built: the service holding its masked queue, and its judge
RunnerFactory = Callable[[RefinementService, Proposer], RefinementRunner]


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


class Population(BaseModel):
    """What one checkout offers each suite: the truths, the collisions, the decoys, the names."""

    model_config = ConfigDict(frozen=True)

    truths: tuple[Truth, ...] = ()
    collisions: tuple[UnresolvedRow, ...] = ()
    #: short name -> every node that carries it, which is where a decoy trial finds its decoys
    decoy_pool: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    names_defined: frozenset[str] = frozenset()
    #: symbol ids a negative trial can hang a defined-nowhere name on
    sources: tuple[str, ...] = ()

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
            truths=_truths(resolve_structural(nodes).edges, nodes, definers, bindings),
            collisions=tuple(row for row in queue if row.externally_bound),
            # every node of the name, not the role-filtered definers: a truth has exactly one of
            # those by construction, so a pool built from them would offer no decoy at all
            decoy_pool={
                name: tuple(ids) for name, ids in named.items() if len(ids) > 1
            },
            names_defined=frozenset(definers),
            sources=tuple(sorted(node.id for node in nodes if "::" in node.id)),
        )

    def counts_by_stratum(self) -> dict[str, int]:
        """How many truths each add stratum offers, which is what `--sample` is capped by."""
        counts = {stratum.value: 0 for stratum in Stratum}
        for truth in self.truths:
            counts[truth.stratum.value] += 1
        return counts


def _truths(
    edges: Sequence[GraphEdge],
    nodes: Sequence[GraphNode],
    definers: Mapping[str, Sequence[str]],
    bindings: NameBindings,
) -> tuple[Truth, ...]:
    """Spec 10.2's tier B shape: a resolved `calls` edge whose site is a bare or `self` call on a
    name this repo defines exactly once, at that destination, and binds from nowhere outside."""
    by_id = {node.id: node for node in nodes}
    out: list[Truth] = []
    for edge in edges:
        node = by_id.get(edge.src)
        if edge.kind is not EdgeKind.CALLS or node is None:
            continue
        name = short_name(edge.dst)
        found = form_for(call_forms(node), name, node.local_names)
        if found is None or found[0] not in BOUNDED_FORMS:
            continue
        call_form, receivers = found
        # the definers a real queue row would carry must be this destination alone (invariant 5)
        if tuple(definers.get(name, ())) != (edge.dst,):
            continue
        if bindings.externally_bound(file_of(edge.src), *receivers):
            continue
        out.append(
            Truth(
                src=edge.src,
                dst=edge.dst,
                name=name,
                edge_kind=EdgeKind.CALLS,
                call_form=call_form,
                receiver_root=receivers[0] if receivers else None,
                stratum=Stratum.of(
                    edge.src,
                    edge.dst,
                    imports=bindings.imported_module_ids(file_of(edge.src)),
                ),
            )
        )
    return _without_multi_target(out)


def _without_multi_target(truths: Sequence[Truth]) -> tuple[Truth, ...]:
    """Drop a site that resolves one name to two destinations: no single answer is the truth."""
    sites: dict[tuple[str, str], int] = {}
    for truth in truths:
        sites[(truth.src, truth.name)] = sites.get((truth.src, truth.name), 0) + 1
    return tuple(truth for truth in truths if sites[(truth.src, truth.name)] == 1)


class Trial(BaseModel):
    """One masked row a runner is asked to answer, and the answer it is judged against."""

    model_config = ConfigDict(frozen=True)

    suite: EvalSuite
    stratum: EvalStratum
    row: UnresolvedRow
    #: the masked destination for `add` and `decoy`; a control has no right answer to name
    truth: str | None = None
    edge_kind: EdgeKind | None = None

    @property
    def key(self) -> tuple[str, str]:
        """What a proposal is matched to its trial by."""
        return (self.row.node_id, self.row.name)


class Judgement(BaseModel):
    """One trial and what the runner did with it."""

    model_config = ConfigDict(frozen=True)

    trial: Trial
    proposed: tuple[Proposal, ...] = ()
    verdict: str


def sample(
    population: Population, *, suite: EvalSuite, size: int, seed: int
) -> tuple[Trial, ...]:
    """Draw at most ``size`` trials per stratum, deterministically under ``seed`` (spec 10.2).

    A suite stored under one stratum has exactly one, so it draws ``min(size, available)`` in all.
    """
    rng = random.Random(seed)
    if suite is EvalSuite.ADD:
        return _adds(population, rng, size)
    if suite is EvalSuite.DECOY:
        return _decoys(population, rng, size)
    if suite is EvalSuite.COLLISION:
        rows = list(population.collisions)
        rng.shuffle(rows)
        return tuple(_control(EvalSuite.COLLISION, row) for row in rows[:size])
    return _negatives(population, rng, size)


def _adds(population: Population, rng: random.Random, size: int) -> tuple[Trial, ...]:
    """The add suite: each stratum shuffled and capped alone, so one run holds one stratum."""
    out: list[Trial] = []
    for stratum in Stratum:
        truths = [truth for truth in population.truths if truth.stratum is stratum]
        rng.shuffle(truths)
        out.extend(_masked(truth) for truth in truths[:size])
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
        ),
        truth=truth.dst,
        edge_kind=truth.edge_kind,
    )


def _decoys(population: Population, rng: random.Random, size: int) -> tuple[Trial, ...]:
    """The decoy suite: the same truths, offered as candidates the runner has to choose between."""
    truths = list(population.truths)
    rng.shuffle(truths)
    out: list[Trial] = []
    for truth in truths[:size]:
        candidates = [truth.dst, *_decoys_for(population, truth)]
        rng.shuffle(candidates)
        out.append(
            Trial(
                suite=EvalSuite.DECOY,
                stratum=CONTROL_STRATUM,
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


def _decoys_for(population: Population, truth: Truth) -> list[str]:
    """Up to three wrong answers, best first: other nodes of the same short name, then the
    destination's own module, then the rest of the graph.

    The last fallback is what keeps a trial from offering one candidate, which would be a free
    point rather than a choice.
    """
    same_name = population.decoy_pool.get(truth.name, ())
    same_module = [
        node_id
        for node_id in population.sources
        if file_of(node_id) == file_of(truth.dst)
    ]
    out: list[str] = []
    for node_id in (*same_name, *same_module, *population.sources):
        if node_id != truth.dst and node_id not in out:
            out.append(node_id)
        if len(out) == DECOY_COUNT:
            break
    return out


def _negatives(
    population: Population, rng: random.Random, size: int
) -> tuple[Trial, ...]:
    """The negative suite: names this repo defines nowhere, asked about at real source nodes."""
    sources = list(population.sources)
    rng.shuffle(sources)
    out: list[Trial] = []
    for index, node_id in enumerate(sources):
        if len(out) == size:
            break
        name = f"{short_name(node_id)}_missing{index}"
        if name in population.names_defined:
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


def _control(suite: EvalSuite, row: UnresolvedRow) -> Trial:
    """One control trial: no right answer to name, so any add is a false add."""
    return Trial(suite=suite, stratum=CONTROL_STRATUM, row=row)


def batches(trials: Sequence[Trial], size: int) -> tuple[tuple[Trial, ...], ...]:
    """Group trials into runs of at most ``size``, never mixing strata (spec 10.2).

    A run then belongs to exactly one `graph_evals` row, so its cost and turns need no
    apportioning.
    """
    out: list[tuple[Trial, ...]] = []
    for stratum in dict.fromkeys(str(trial.stratum) for trial in trials):
        group = [trial for trial in trials if str(trial.stratum) == stratum]
        out.extend(
            tuple(group[start : start + size]) for start in range(0, len(group), size)
        )
    return tuple(out)


class Judge(BaseModel):
    """Scores one batch's proposals against its trials, and stores none of them (spec 10.2).

    A mutable aggregate like `BoundTools`: filled in as the run proceeds, read when it ends.
    """

    model_config = ConfigDict(frozen=False)

    trials: Mapping[tuple[str, str], Trial]
    seen: dict[tuple[str, str], list[Proposal]] = Field(default_factory=dict)

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
        key = (target.node_id or target.src or "", target.name or "")
        self.seen.setdefault(key if key in self.trials else OFF_TARGET, []).append(
            proposal
        )
        if complaint:
            return Verdict(
                outcome=ProposalOutcome.REJECTED, kind=proposal.kind, detail=complaint
            )
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
        return tuple(self.seen.get(OFF_TARGET, ()))

    def judgements(self) -> tuple[Judgement, ...]:
        """One judgement per trial, whatever the runner did or did not propose."""
        return tuple(
            Judgement(
                trial=trial,
                proposed=tuple(self.seen.get(key, ())),
                verdict=verdict_of(trial, tuple(self.seen.get(key, ()))),
            )
            for key, trial in self.trials.items()
        )


def verdict_of(trial: Trial, proposed: Sequence[Proposal]) -> str:
    """What one trial's proposals earned, per spec 10.2's five outcomes."""
    added = [p for p in proposed if p.kind is RefinementKind.ADD_EDGE]
    chosen = [p for p in proposed if p.kind is RefinementKind.RESOLVE_AMBIGUOUS]
    if trial.suite is EvalSuite.COLLISION:
        return "false_add" if added else "clean"
    if trial.suite is EvalSuite.NEGATIVE:
        return "false_add" if added or chosen else "clean"
    if trial.suite is EvalSuite.DECOY:
        if len(chosen) == 1 and chosen[0].payload.candidate == trial.truth:
            return "correct"
        # an `add_edge` the brief never offered is a wrong answer, not a missing one
        return "wrong" if chosen or added else "missed"
    if not added:
        return "missed"
    if (
        len(added) == 1
        and added[0].target.dst == trial.truth
        and added[0].target.edge_kind is trial.edge_kind
    ):
        return "correct"
    return "wrong"


class SuiteSpend(BaseModel):
    """What one stratum's runs cost, summed off the closed rows (spec 5.3)."""

    model_config = ConfigDict(frozen=True)

    cost_usd: float = 0.0
    num_turns: int = 0
    runs: int = 0

    def plus(self, usage: RunUsage) -> "SuiteSpend":
        return SuiteSpend(
            cost_usd=self.cost_usd + usage.cost_usd,
            num_turns=self.num_turns + usage.num_turns,
            runs=self.runs + 1,
        )


def tally(
    judgements: Sequence[Judgement],
    *,
    suite: EvalSuite,
    spend: Mapping[str, SuiteSpend],
) -> tuple[SuiteTally, ...]:
    """Sum one suite's judgements per stratum, with the cost and turns its runs spent.

    ``spend`` is read off the closed run rows: a `RunProduct.run` is the row as it was opened, so
    its usage is still empty and summing that would report every eval as free.
    """
    grouped: dict[str, list[Judgement]] = {}
    for judgement in judgements:
        grouped.setdefault(str(judgement.trial.stratum), []).append(judgement)
    out: list[SuiteTally] = []
    for stratum, group in grouped.items():
        counts = Counter(judgement.verdict for judgement in group)
        got = spend.get(stratum, SuiteSpend())
        out.append(
            SuiteTally(
                suite=suite.value,
                stratum=group[0].trial.stratum,
                n=len(group),
                correct=counts["correct"],
                wrong=counts["wrong"],
                missed=counts["missed"],
                false_adds=counts["false_add"],
                cost_usd=got.cost_usd,
                num_turns=got.num_turns,
                runs=got.runs,
            )
        )
    return tuple(out)


def key_of(suite: str, stratum: EvalStratum) -> str:
    """How a report names one measured stratum, which is the key the go/no-go reads."""
    return f"{suite}/{stratum}"


class SuiteResult(BaseModel):
    """One suite's measured strata, and the sentences the report says about the draw."""

    model_config = ConfigDict(frozen=True)

    tallies: tuple[SuiteTally, ...] = ()
    #: strata that drew fewer trials than asked for, or whose suite a run cut short
    short: tuple[str, ...] = ()
    #: strata with nothing to draw, which measure nothing and so prove nothing
    empty: tuple[str, ...] = ()


async def run_eval(
    service: RefinementService,
    *,
    build: RunnerFactory,
    runner: RunnerKind,
    model: ClaudeModel | None,
    suites: Sequence[EvalSuite],
    size: int,
    seed: int,
) -> EvalReport:
    """Measure ``suites`` on this checkout and store one `graph_evals` row per stratum.

    ``build`` makes each batch's runner, so this module never imports the one that selects them.
    A run that fails or aborts stops its suite, and the rows written cover only judged trials.
    """
    user = service.user
    min_precision = user.observer.tuning.min_precision
    population = await Population.of(service.facts)
    drawn = {
        suite: sample(population, suite=suite, size=size, seed=seed) for suite in suites
    }
    plan = {
        suite: batches(trials, user.observer.limits.max_nodes_per_run)
        for suite, trials in drawn.items()
    }
    results: list[SuiteResult] = []
    for suite, groups in plan.items():
        measured = await _measure(service, suite, groups, build=build, model=model)
        results.append(
            SuiteResult(
                tallies=measured.tallies,
                short=measured.short + _short(suite, drawn[suite], size),
                empty=_empty(suite, drawn[suite]),
            )
        )
    tallies = tuple(got for result in results for got in result.tallies)
    identity = service.identity
    for got in tallies:
        await service.index.evals.add_eval(
            got.row(identity=identity, runner=runner, model=model or "")
        )
    policy = TierPolicy.of(
        await service.index.evals.latest(runner, model or ""),
        min_precision=min_precision,
        runner=runner,
        model=model or "",
    )
    return EvalReport(
        runner=runner,
        model=model or "",
        sample=size,
        seed=seed,
        min_precision=min_precision,
        suites=tallies,
        short=tuple(line for result in results for line in result.short),
        empty=tuple(line for result in results for line in result.empty),
        unprovable=_unprovable(tallies, min_precision),
        proven=tuple(sorted(key_of(*pair) for pair in policy.proven)),
        cost_usd=sum(got.cost_usd for got in tallies),
        runs=sum(got.runs for got in tallies),
        runs_planned=sum(len(groups) for groups in plan.values()),
    )


def _strata_of(suite: EvalSuite) -> tuple[EvalStratum, ...]:
    """The strata one suite is stored under: the add strata, or the controls' one bucket (P2)."""
    return tuple(Stratum) if suite is EvalSuite.ADD else (CONTROL_STRATUM,)


def _drawn_in(trials: Sequence[Trial], stratum: EvalStratum) -> int:
    return sum(1 for trial in trials if str(trial.stratum) == str(stratum))


def _empty(suite: EvalSuite, trials: Sequence[Trial]) -> tuple[str, ...]:
    """The strata this repo had nothing to draw for."""
    return tuple(
        key_of(suite.value, stratum)
        for stratum in _strata_of(suite)
        if _drawn_in(trials, stratum) == 0
    )


def _short(suite: EvalSuite, trials: Sequence[Trial], size: int) -> tuple[str, ...]:
    """The strata that had something to draw but less than ``--sample`` asked for."""
    return tuple(
        f"{key_of(suite.value, stratum)}: drew {drawn} of {size}"
        for stratum in _strata_of(suite)
        if 0 < (drawn := _drawn_in(trials, stratum)) < size
    )


def _unprovable(tallies: Sequence[SuiteTally], min_precision: float) -> tuple[str, ...]:
    """The strata whose draw cannot clear ``min_precision`` however flawless the run (spec 10.4).

    Only the two suites a Wilson bound gates: a control clears on having produced no false add,
    which one trial can do.
    """
    floor = flawless_floor(min_precision)
    gated = {suite.value for suite in PRECISION_SUITES}
    return tuple(
        f"{key_of(got.suite, got.stratum)}: {got.n} trials, below the {floor} a flawless run "
        f"needs at {min_precision}"
        for got in tallies
        if got.suite in gated and 0 < got.n < floor
    )


async def _measure(
    service: RefinementService,
    suite: EvalSuite,
    groups: Sequence[Sequence[Trial]],
    *,
    build: RunnerFactory,
    model: ClaudeModel | None,
) -> SuiteResult:
    """Drive one suite's batches, stopping at the first run that did not succeed."""
    judged: list[Judgement] = []
    spend: dict[str, SuiteSpend] = {}
    stopped: list[str] = []
    for group in groups:
        judge = Judge.over(group)
        row = await _one_batch(service, group, judge, build=build, model=model)
        key = key_of(suite.value, group[0].stratum)
        if row is None:
            stopped.append(
                f"{key}: the run never opened, so its trials are not counted"
            )
            break
        if row.status is not RunStatus.SUCCEEDED:
            stopped.append(
                f"{key}: a run ended {row.status.value}, so the suite stopped there"
            )
            break
        judged.extend(judge.judgements())
        stratum = str(group[0].stratum)
        spend[stratum] = spend.get(stratum, SuiteSpend()).plus(row.usage)
    return SuiteResult(
        tallies=tally(judged, suite=suite, spend=spend), short=tuple(stopped)
    )


async def _one_batch(
    service: RefinementService,
    trials: Sequence[Trial],
    judge: Judge,
    *,
    build: RunnerFactory,
    model: ClaudeModel | None,
) -> Run | None:
    """One run over one batch's masked queue, and its row re-read after it closed."""
    masked = RefinementService(
        service.index,
        service.root,
        service.settings,
        service.user,
        facts=service.facts.model_copy(
            update={"synthetic": tuple(trial.row for trial in trials)}
        ),
    )
    try:
        product = await build(masked, judge.propose).run(
            RefinementJob(
                scope="",
                trigger=TriggerKind.EVAL,
                producer=ProducerKind.CLI,
                client=ClientKind.CLI,
                model=model,
            )
        )
    except RefinementRefused as exc:
        logger.warning("eval batch refused: %s", exc)
        return None
    return await service.index.runs.run(product.run.run_id)
