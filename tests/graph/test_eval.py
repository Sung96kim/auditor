"""The eval suites: the population they draw from, the judge, the arithmetic and the rows."""

import random
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from graph._support import eval_build

import auditor
from auditor.cli.helpers import load_settings, load_user, open_index
from auditor.cli.options import EVAL_SAMPLE_DEFAULT
from auditor.database import IndexStore
from auditor.graph.model import CallForm, EdgeKind, FactKind, UnresolvedRow
from auditor.graph.refine import tiers
from auditor.graph.refine.brief import BriefTarget
from auditor.graph.refine.eval import (
    DECOY_COUNT,
    JUDGED,
    EvalRun,
    EvalSuiteSpec,
    ExclusionRule,
    Judge,
    Judgement,
    Population,
    Trial,
    Truth,
    batches,
    tally,
)
from auditor.graph.refine.models import (
    ALL_SUITES,
    BOUNDED_FORMS,
    PRECISION_SUITES,
    EvalSuite,
    Proposal,
    ProposalOutcome,
    Refinement,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    RefusalKind,
    RunnerKind,
    RunStatus,
    RunUsage,
    Stratum,
    SuiteSpend,
    Tier,
    TriggerKind,
    flawless_floor,
    key_of,
    wilson_lower,
)
from auditor.graph.refine.payloads import EvalPlan, EvalReport
from auditor.graph.refine.runner import FakeRun
from auditor.graph.refine.service import (
    RefinementRefused,
    RefinementService,
    RunRegistry,
)
from auditor.graph.refine.tiers import TierPolicy
from auditor.observer import assess
from auditor.user_settings import BudgetConfig, LimitsConfig

#: the four truths `graph_repo_eval` is built to yield, exactly (P6)
EXPECTED_TRUTHS = {
    ("m.py::calls_same", "m.py::same_target", "bare", "same-module"),
    ("m.py::calls_direct", "lib.py::direct_target", "bare", "direct-import"),
    ("m.py::calls_reexported", "pkg/deep.py::reexported", "bare", "neither"),
    ("m.py::Holder.uses", "m.py::Holder.helper", "self", "same-module"),
}
#: the resolved edges the ground truth leaves out, and the one rule that leaves each out
EXCLUDED_EDGES = {
    ExclusionRule.NOT_BOUNDED_FORM: (
        ("attrs.py::via_attr", "m.py::Holder.helper"),
        ("shadow.py::shadowed_call", "lib.py::direct_target"),
    ),
    ExclusionRule.NOT_SOLE_DEFINER: (
        ("tests/stub.py::uses_same", "tests/stub.py::same_target"),
    ),
    ExclusionRule.EXTERNALLY_BOUND: (
        ("extbound.py::calls_escape", "extbound.py::escape"),
    ),
}


def _truth(**kw: Any) -> Truth:
    return Truth(
        src=kw.pop("src", "a.py::f"),
        dst=kw.pop("dst", "a.py::g"),
        name=kw.pop("name", "g"),
        edge_kind=EdgeKind.CALLS,
        call_form=kw.pop("call_form", CallForm.BARE),
        stratum=kw.pop("stratum", Stratum.SAME_MODULE),
        **kw,
    )


def _add_trial(**kw: Any) -> Trial:
    return Trial(
        suite=EvalSuite.ADD,
        stratum=kw.pop("stratum", Stratum.SAME_MODULE),
        row=UnresolvedRow(
            node_id=kw.pop("src", "a.py::f"),
            fact_kind=FactKind.CALLEE,
            name=kw.pop("name", "g"),
            reason="unimportable_name",
            definers=("a.py::g",),
        ),
        truth=kw.pop("truth", "a.py::g"),
        edge_kind=EdgeKind.CALLS,
    )


def _proposal(kind: RefinementKind, **kw: Any) -> dict[str, Any]:
    return Proposal(
        kind=kind,
        target=RefinementTarget(
            src=kw.pop("src", "a.py::f"),
            dst=kw.pop("dst", None),
            node_id=kw.pop("node_id", None),
            edge_kind=kw.pop("edge_kind", EdgeKind.CALLS),
            name=kw.pop("name", "g"),
        ),
        payload=RefinementPayload(candidate=kw.pop("candidate", None)),
        reason="the call site and the definition both read",
    ).model_dump()


@pytest.fixture
async def eval_index(eval_repo: Path):
    """The built eval package's index, open for the length of one test."""
    async with await open_index(eval_repo) as index:
        yield index


@pytest.fixture
def eval_service(eval_repo: Path, eval_index: IndexStore) -> RefinementService:
    return RefinementService(
        eval_index,
        eval_repo,
        load_settings(eval_repo),
        load_user(eval_repo),
        registry=RunRegistry(),
    )


@pytest.fixture
async def population(eval_service: RefinementService) -> Population:
    return await Population.of(eval_service.facts)


# ---------------------------------------------------------------- the arithmetic


@pytest.mark.parametrize(
    ("correct", "total", "bound"),
    [
        (80, 80, 0.954),
        (0, 0, 0.0),
        (1, 1, 0.207),
        (6, 6, 0.610),
        (38, 38, 0.908),
        (76, 80, 0.878),
    ],
)
def test_the_wilson_lower_bound_is_what_the_spec_says(correct, total, bound):
    assert round(wilson_lower(correct, total), 3) == bound


def test_the_wilson_bound_rises_with_the_correct_count():
    bounds = [wilson_lower(correct, 20) for correct in range(21)]
    assert bounds == sorted(bounds)


def test_seventy_three_flawless_trials_are_the_smallest_run_that_clears_the_default():
    """The spec's 80 give 0.954, but 73 is the floor, which is what an unprovable stratum reads."""
    assert flawless_floor(0.95) == 73
    assert wilson_lower(72, 72) < 0.95 <= wilson_lower(73, 73)


def test_a_lower_bar_moves_the_floor_down():
    assert flawless_floor(0.5) == 4


def test_a_bar_no_run_can_meet_answers_none_rather_than_searching_forever():
    """`wilson_lower(n, n)` is below 1.0 for every finite `n`, so 1.0 used to hang the command
    after every run had already been paid for."""
    assert flawless_floor(1.0) is None
    assert flawless_floor(0.999) is not None


#: the per-stratum truth counts this repo measures, published in one place and read back from it
POPULATION_COUNTS = "883 / 1,321 / 38"


def test_the_published_population_counts_agree_wherever_they_appear():
    """Two docs quoting different counts is how an operator sizes a go/no-go off a figure the tool
    will never produce."""
    root = Path(auditor.__file__).resolve().parent.parent
    pages = [
        path
        for parent in (root / "docs", root / "plugin")
        for path in parent.rglob("*.md")
        if "superpowers" not in path.parts
    ] + [root / "README.md"]
    pattern = re.compile(r"[\d,]+ / [\d,]+ / \d+(?= tier-B-shaped truths)")
    found = {match for page in pages for match in pattern.findall(page.read_text())}
    assert found == {POPULATION_COUNTS}
    assert POPULATION_COUNTS in (Stratum.__doc__ or "")


# ---------------------------------------------------------------- the population


async def test_the_ground_truth_is_every_bounded_single_definer_call(population):
    got = {
        (t.src, t.dst, t.call_form.value, t.stratum.value) for t in population.truths
    }
    assert got == EXPECTED_TRUTHS


@pytest.mark.parametrize("rule", list(EXCLUDED_EDGES))
async def test_each_ground_truth_rule_removes_exactly_its_own_edges(population, rule):
    """Every rule has a resolved edge only it excludes, so dropping the rule adds a truth."""
    assert population.ground.excluded_by(rule) == tuple(sorted(EXCLUDED_EDGES[rule]))


async def test_the_ground_truth_is_the_resolved_edges_minus_the_exclusions(population):
    excluded = sum(len(edges) for edges in EXCLUDED_EDGES.values())
    assert len(population.ground.sites) == len(EXPECTED_TRUTHS) + excluded


async def test_the_strata_are_counted_as_the_package_lays_them_out(population):
    counts = {stratum.value: 0 for stratum in Stratum.add_strata()}
    for truth in population.truths:
        counts[truth.stratum.value] += 1
    assert counts == {"same-module": 2, "direct-import": 1, "neither": 1}


async def test_a_self_call_keeps_the_receiver_the_verifier_reads(population):
    method = next(t for t in population.truths if t.call_form is CallForm.SELF)
    assert (method.receiver_root, method.src) == ("self", "m.py::Holder.uses")
    assert all(
        t.receiver_root is None
        for t in population.truths
        if t.call_form is CallForm.BARE
    )


async def test_the_collision_rows_are_the_externally_bound_queue_rows(population):
    assert [(row.node_id, row.name) for row in population.collisions] == [
        ("m.py::externally_bound_call", "match")
    ]
    assert all(row.externally_bound for row in population.collisions)


async def test_the_decoy_pool_holds_the_names_more_than_one_node_carries(population):
    assert population.decoy_pool == {
        "same_target": ("m.py::same_target", "tests/stub.py::same_target")
    }


async def test_names_defined_is_the_role_filtered_index(population):
    assert "same_target" in population.names_defined
    assert "only_in_tests" not in population.names_defined


# ---------------------------------------------------------------- the suite registry


def test_a_suite_this_build_cannot_draw_raises_rather_than_measuring_another():
    """`fixtures` used to fall through to the negative suite and be stored under `suite=fixtures`,
    which the gate then read as a precision-gated row."""
    with pytest.raises(ValueError, match="fixtures has no draw"):
        EvalSuiteSpec.of(EvalSuite.FIXTURES)


@pytest.mark.parametrize("suite", ALL_SUITES)
def test_every_shipped_suite_has_a_spec_that_agrees_with_the_vocabulary(suite):
    spec = EvalSuiteSpec.of(suite)
    assert spec.SUITE is suite
    assert spec.DRAW.__name__ in vars(Population)
    assert spec.precision_gated == (suite in PRECISION_SUITES)


def test_the_gate_and_the_eval_read_one_list_of_precision_suites():
    """Two lists a module apart already disagreed on `fixtures`: the gate would bound a stratum
    the eval would never warn about. The observer's low budget narrowing is the third reader."""
    assert {suite.value for suite in PRECISION_SUITES} == tiers._PRECISION_SUITES
    assert set(BOUNDED_FORMS) == tiers._BOUNDED_FORMS == assess._BOUNDED_FORMS


# ---------------------------------------------------------------- sampling


@pytest.mark.parametrize("suite", ALL_SUITES)
async def test_the_same_seed_draws_the_same_trials(population, suite):
    first = population.sample(suite=suite, size=2, seed=7)
    assert first == population.sample(suite=suite, size=2, seed=7)


@pytest.mark.parametrize(
    ("suite", "size"),
    [(EvalSuite.ADD, 1), (EvalSuite.DECOY, 4), (EvalSuite.NEGATIVE, 2)],
)
async def test_a_different_seed_draws_a_different_answer(population, suite, size):
    """A seed nothing reads would satisfy the determinism test on its own."""
    drawn = {
        tuple(
            (trial.row.node_id, trial.row.name, trial.row.candidates)
            for trial in population.sample(suite=suite, size=size, seed=seed)
        )
        for seed in range(12)
    }
    assert len(drawn) > 1


async def test_the_decoys_a_seed_draws_are_the_same_decoys_next_time(population):
    first = population.sample(suite=EvalSuite.DECOY, size=4, seed=3)
    assert first == population.sample(suite=EvalSuite.DECOY, size=4, seed=3)
    other = population.sample(suite=EvalSuite.DECOY, size=4, seed=11)
    assert [t.row.candidates for t in first] != [t.row.candidates for t in other]


async def test_a_batch_of_decoy_trials_never_offers_one_distractor_twice(population):
    """The true destination must not be the one candidate that changes from trial to trial."""
    trials = population.sample(suite=EvalSuite.DECOY, size=4, seed=3)
    decoys = [c for t in trials for c in t.row.candidates if c != t.truth]
    assert len(decoys) == len(set(decoys))


async def test_a_decoy_is_a_node_of_the_destinations_own_kind_while_any_are_left(
    population,
):
    """A class offered in place of a function is a candidate the model need not read to reject."""
    for truth in population.truths:
        want = population.kinds[truth.dst]
        pool = [
            node
            for node, kind in population.kinds.items()
            if kind == want and node != truth.dst and "::" in node
        ]
        decoys = population.decoys_for(truth, random.Random(3))
        assert len(decoys) == DECOY_COUNT
        assert sum(1 for d in decoys if population.kinds[d] == want) == min(
            DECOY_COUNT, len(pool)
        )


async def test_a_stratum_draws_no_more_than_it_has(population):
    trials = population.sample(suite=EvalSuite.ADD, size=50, seed=1)
    assert len(trials) == len(population.truths)


async def test_the_add_suite_draws_per_stratum(population):
    trials = population.sample(suite=EvalSuite.ADD, size=1, seed=1)
    assert sorted(str(t.stratum) for t in trials) == [
        "direct-import",
        "neither",
        "same-module",
    ]


async def test_every_add_trial_looks_like_the_row_the_queue_would_have_written(
    population,
):
    for trial in population.sample(suite=EvalSuite.ADD, size=50, seed=1):
        assert trial.row.definers == (trial.truth,)
        assert trial.row.call_form in (CallForm.BARE, CallForm.SELF)
        assert trial.row.reason.value == "unimportable_name"
        assert trial.stratum is not Stratum.ALL


async def test_a_decoy_trial_offers_the_truth_once_and_never_add_edge(
    population,
):
    for trial in population.sample(suite=EvalSuite.DECOY, size=50, seed=3):
        assert trial.row.candidates.count(trial.truth) == 1
        assert 1 < len(trial.row.candidates) <= DECOY_COUNT + 1
        assert trial.row.definers == ()
        allowed = BriefTarget.of(trial.row, path="m.py", line=1, facts=()).allowed
        assert RefinementKind.ADD_EDGE not in allowed
        assert RefinementKind.RESOLVE_AMBIGUOUS in allowed
        assert trial.stratum is Stratum.ALL


async def test_a_negative_trial_names_nothing_this_repo_defines(population):
    trials = population.sample(suite=EvalSuite.NEGATIVE, size=4, seed=5)
    assert trials
    for trial in trials:
        assert trial.row.name not in population.names_defined
        assert trial.truth is None
        assert BriefTarget.of(trial.row, path="m.py", line=1, facts=()).allowed == (
            RefinementKind.ANNOTATE_NODE,
            RefinementKind.UNRESOLVABLE,
        )


async def test_a_collision_trial_is_a_real_queue_row(population):
    (trial,) = population.sample(suite=EvalSuite.COLLISION, size=9, seed=1)
    assert trial.row in population.collisions
    assert trial.truth is None


def test_batches_never_mix_strata():
    trials = (
        _add_trial(src="a.py::f", stratum=Stratum.SAME_MODULE),
        _add_trial(src="a.py::g", stratum=Stratum.NEITHER),
        _add_trial(src="a.py::h", stratum=Stratum.SAME_MODULE),
    )
    groups = batches(trials, 2)
    assert [len(group) for group in groups] == [2, 1]
    assert all(len({str(t.stratum) for t in group}) == 1 for group in groups)


def test_batches_cap_at_the_run_limit():
    trials = tuple(_add_trial(src=f"a.py::f{i}") for i in range(5))
    assert [len(group) for group in batches(trials, 2)] == [2, 2, 1]


# ---------------------------------------------------------------- the judge


@pytest.mark.parametrize(
    ("suite", "kind", "extra", "verdict"),
    [
        (EvalSuite.ADD, RefinementKind.ADD_EDGE, {"dst": "a.py::g"}, "correct"),
        (EvalSuite.ADD, RefinementKind.ADD_EDGE, {"dst": "a.py::wrong"}, "wrong"),
        (EvalSuite.ADD, RefinementKind.UNRESOLVABLE, {}, "missed"),
        (EvalSuite.ADD, None, {}, "missed"),
        (
            EvalSuite.DECOY,
            RefinementKind.RESOLVE_AMBIGUOUS,
            {"candidate": "a.py::g"},
            "correct",
        ),
        (
            EvalSuite.DECOY,
            RefinementKind.RESOLVE_AMBIGUOUS,
            {"candidate": "a.py::wrong"},
            "wrong",
        ),
        (EvalSuite.DECOY, RefinementKind.ADD_EDGE, {"dst": "a.py::g"}, "wrong"),
        (EvalSuite.DECOY, None, {}, "missed"),
        (EvalSuite.COLLISION, RefinementKind.ADD_EDGE, {"dst": "a.py::g"}, "false_add"),
        (EvalSuite.COLLISION, RefinementKind.UNRESOLVABLE, {}, "clean"),
        (EvalSuite.COLLISION, None, {}, "clean"),
        (EvalSuite.NEGATIVE, RefinementKind.ADD_EDGE, {"dst": "a.py::g"}, "false_add"),
        (
            EvalSuite.NEGATIVE,
            RefinementKind.RESOLVE_AMBIGUOUS,
            {"candidate": "a.py::g"},
            "false_add",
        ),
        (EvalSuite.NEGATIVE, RefinementKind.ANNOTATE_NODE, {}, "clean"),
    ],
)
async def test_every_verdict_rule(suite, kind, extra, verdict):
    trial = _add_trial().model_copy(
        update={
            "suite": suite,
            "truth": "a.py::g" if suite is not EvalSuite.COLLISION else None,
        }
    )
    if suite in (EvalSuite.COLLISION, EvalSuite.NEGATIVE):
        trial = trial.model_copy(update={"truth": None})
    judge = Judge.over([trial])
    if kind is not None:
        await judge.propose("run-1", _judgeable(kind, trial, extra))
    (judgement,) = judge.judgements()
    assert judgement.verdict == verdict


def _judgeable(
    kind: RefinementKind, trial: Trial, extra: Mapping[str, Any]
) -> dict[str, Any]:
    """One raw payload aimed at ``trial``, in the shape its kind needs."""
    if kind is RefinementKind.RESOLVE_AMBIGUOUS:
        return _proposal(
            kind, src=None, node_id=trial.row.node_id, name=trial.row.name, **extra
        )
    if kind in (RefinementKind.ANNOTATE_NODE, RefinementKind.UNRESOLVABLE):
        return {
            "kind": kind.value,
            "target": {"node_id": trial.row.node_id, "name": trial.row.name},
            "payload": {"annotation": "nothing defines it"}
            if kind is RefinementKind.ANNOTATE_NODE
            else {"reason_code": "unimportable_name"},
            "reason": "there is nothing here to point at",
        }
    return _proposal(kind, src=trial.row.node_id, name=trial.row.name, **extra)


async def test_two_adds_for_one_trial_are_wrong_not_correct():
    trial = _add_trial()
    judge = Judge.over([trial])
    for dst in ("a.py::g", "a.py::other"):
        await judge.propose("run-1", _proposal(RefinementKind.ADD_EDGE, dst=dst))
    assert judge.judgements()[0].verdict == "wrong"


async def test_an_add_of_the_wrong_edge_kind_is_wrong():
    judge = Judge.over([_add_trial()])
    await judge.propose(
        "run-1",
        _proposal(
            RefinementKind.ADD_EDGE, dst="a.py::g", edge_kind=EdgeKind.REFERENCES_TYPE
        ),
    )
    assert judge.judgements()[0].verdict == "wrong"


async def test_a_proposal_about_nothing_the_batch_asked_lands_off_target():
    judge = Judge.over([_add_trial()])
    await judge.propose(
        "run-1",
        _proposal(RefinementKind.ADD_EDGE, src="z.py::q", name="q", dst="z.py::r"),
    )
    assert len(judge.off_target) == 1
    assert judge.judgements()[0].verdict == "missed"


async def test_the_judge_answers_a_legal_proposal_without_storing_it():
    judge = Judge.over([_add_trial()])
    verdict = await judge.propose(
        "run-1", _proposal(RefinementKind.ADD_EDGE, dst="a.py::g")
    )
    assert verdict.outcome is ProposalOutcome.STAGED
    assert (verdict.tier, verdict.status, verdict.detail) == (
        Tier.C,
        RefinementStatus.PENDING,
        JUDGED,
    )


async def test_a_readable_but_illegal_payload_is_rejected_and_not_staged():
    """`Proposal.read`'s lenient arm: the model sees the shape a real run would answer with."""
    judge = Judge.over([_add_trial()])
    verdict = await judge.propose(
        "run-1", {"kind": "add_edge", "target": {}, "reason": ""}
    )
    assert verdict.outcome is ProposalOutcome.REJECTED
    assert verdict.kind is RefinementKind.ADD_EDGE
    assert verdict.refusal is RefusalKind.INVALID
    assert judge.off_target == ()


async def test_an_on_target_payload_the_validators_refuse_scores_for_nothing():
    """The refusal used to be filed as an answer and re-validated when the judgement was built,
    which crashed the whole eval after every batch had been paid for."""
    trial = _add_trial()
    judge = Judge.over([trial])
    verdict = await judge.propose(
        "run-1",
        {
            "kind": "add_edge",
            "target": {
                "src": trial.row.node_id,
                "dst": trial.truth,
                "edge_kind": "calls",
                "name": trial.row.name,
            },
            "reason": "",
        },
    )
    assert verdict.outcome is ProposalOutcome.REJECTED
    (judgement,) = judge.judgements()
    assert judgement.verdict == "missed"
    assert any("needs a reason" in note for note in judgement.refusals)


#: three payloads aimed at a node no trial in the batch asked about, one per kind
OFF_TARGET_PAYLOADS = {
    RefinementKind.ADD_EDGE: {
        "kind": "add_edge",
        "target": {
            "src": "z.py::q",
            "dst": "z.py::r",
            "edge_kind": "calls",
            "name": "r",
        },
        "reason": "it reads that way from here",
    },
    RefinementKind.RESOLVE_AMBIGUOUS: {
        "kind": "resolve_ambiguous",
        "target": {"node_id": "z.py::q", "name": "r", "edge_kind": "calls"},
        "payload": {"candidate": "z.py::r"},
        "reason": "the first candidate is the one this call site reaches",
    },
    RefinementKind.ANNOTATE_NODE: {
        "kind": "annotate_node",
        "target": {"node_id": "z.py::q", "name": "r"},
        "payload": {"annotation": "nothing defines it"},
        "reason": "there is nothing here to point at",
    },
}


@pytest.mark.parametrize(
    ("kind", "scored"),
    [
        (RefinementKind.ADD_EDGE, 1),
        (RefinementKind.RESOLVE_AMBIGUOUS, 1),
        (RefinementKind.ANNOTATE_NODE, 0),
    ],
)
async def test_an_off_target_proposal_that_adds_or_picks_counts_as_a_false_add(
    kind, scored
):
    """A real run refuses these; a judge that dropped them let a control clear over them."""
    trial = _add_trial().model_copy(
        update={"suite": EvalSuite.COLLISION, "truth": None}
    )
    judge = Judge.over([trial])
    await judge.propose("run-1", OFF_TARGET_PAYLOADS[kind])
    (got,) = tally(
        judge.judgements(),
        suite=EvalSuite.COLLISION,
        spend={},
        off_target={Stratum.SAME_MODULE: judge.off_target},
    )
    assert got.off_target == 1
    assert got.false_adds == scored


async def test_an_off_target_add_enters_the_precision_denominator():
    """A run that answered its one trial right and filed one stray read precision 1.000:
    `false_adds` feeds only `false_add_rate`, which no precision gate reads."""
    trial = _add_trial()
    judge = Judge.over([trial])
    await judge.propose("run-1", _proposal(RefinementKind.ADD_EDGE, dst=trial.truth))
    await judge.propose("run-1", OFF_TARGET_PAYLOADS[RefinementKind.ADD_EDGE])
    (got,) = tally(
        judge.judgements(),
        suite=EvalSuite.ADD,
        spend={},
        off_target={Stratum.SAME_MODULE: judge.off_target},
    )
    assert (got.correct, got.wrong, got.false_adds, got.off_target) == (1, 1, 0, 1)
    assert got.metrics.precision == 0.5
    assert got.metrics.lower_bound_95 == pytest.approx(wilson_lower(1, 2))


async def test_a_payload_with_no_readable_kind_is_refused_outright():
    judge = Judge.over([_add_trial()])
    with pytest.raises(RefinementRefused, match="not a proposal"):
        await judge.propose("run-1", {"target": {}, "reason": "x"})


async def test_every_trial_is_judged_exactly_once():
    trials = [_add_trial(src=f"a.py::f{i}") for i in range(3)]
    judge = Judge.over(trials)
    assert [j.trial for j in judge.judgements()] == trials


# ---------------------------------------------------------------- the arithmetic on rows


def _judgements(*verdicts: str, stratum=Stratum.SAME_MODULE) -> list[Judgement]:
    return [
        Judgement(trial=_add_trial(src=f"a.py::f{i}", stratum=stratum), verdict=v)
        for i, v in enumerate(verdicts)
    ]


def test_the_tally_counts_and_divides():
    (got,) = tally(
        _judgements("correct", "correct", "wrong", "missed"),
        suite=EvalSuite.ADD,
        spend={Stratum.SAME_MODULE: SuiteSpend(cost_usd=0.25, num_turns=9, runs=2)},
        off_target={},
    )
    assert (got.n, got.correct, got.wrong, got.missed) == (4, 2, 1, 1)
    assert got.metrics.precision == pytest.approx(2 / 3)
    assert got.metrics.recall == pytest.approx(0.5)
    assert got.metrics.false_add_rate == pytest.approx(0.25)
    assert got.metrics.lower_bound_95 == pytest.approx(wilson_lower(2, 3))
    assert (got.spend.cost_usd, got.spend.num_turns, got.spend.runs) == (0.25, 9, 2)


def test_a_tally_that_judged_nothing_but_misses_divides_by_zero_safely():
    (got,) = tally(
        _judgements("missed", "missed"), suite=EvalSuite.ADD, spend={}, off_target={}
    )
    assert (got.metrics.precision, got.metrics.lower_bound_95) == (0.0, 0.0)
    assert got.metrics.n == 2


def test_a_control_tally_counts_false_adds_into_the_rate():
    (got,) = tally(
        [
            Judgement(
                trial=_add_trial().model_copy(
                    update={"suite": EvalSuite.COLLISION, "stratum": Stratum.ALL}
                ),
                verdict=verdict,
            )
            for verdict in ("clean", "false_add")
        ],
        suite=EvalSuite.COLLISION,
        spend={},
        off_target={},
    )
    assert (got.false_adds, got.n) == (1, 2)
    assert got.metrics.false_add_rate == pytest.approx(0.5)


def test_the_tally_keeps_the_strata_apart():
    got = tally(
        _judgements("correct", stratum=Stratum.SAME_MODULE)
        + _judgements("wrong", stratum=Stratum.NEITHER),
        suite=EvalSuite.ADD,
        spend={},
        off_target={},
    )
    assert {str(t.stratum): t.n for t in got} == {"same-module": 1, "neither": 1}


def test_the_row_carries_every_column_the_gate_reads():
    (got,) = tally(_judgements("correct"), suite=EvalSuite.ADD, spend={}, off_target={})
    row = got.row(identity="/repo/.git", runner=RunnerKind.CLAUDE, model="haiku")
    assert (row.repo_identity, row.runner, row.model) == (
        "/repo/.git",
        RunnerKind.CLAUDE,
        "haiku",
    )
    assert (row.suite, row.stratum) == ("add", Stratum.SAME_MODULE)
    assert row.metrics.false_removal_rate == 0.0
    assert row.metrics.correct == 1


def test_a_spend_read_off_an_opened_row_would_report_the_eval_as_free():
    """The run row `RunProduct` carries is the row as it was opened, so its usage is still zero."""
    (closed,) = tally(
        _judgements("correct"),
        suite=EvalSuite.ADD,
        spend={Stratum.SAME_MODULE: SuiteSpend(cost_usd=0.4, num_turns=3, runs=1)},
        off_target={},
    )
    (opened,) = tally(
        _judgements("correct"), suite=EvalSuite.ADD, spend={}, off_target={}
    )
    assert closed.spend.cost_usd == 0.4
    assert opened.spend.cost_usd == 0.0


def test_the_report_key_names_the_suite_and_the_stratum():
    assert key_of("add", Stratum.NEITHER) == "add/neither"
    assert key_of("collision", Stratum.ALL) == "collision/all"


# ---------------------------------------------------------------- end to end


def _answers(
    trials: Sequence[Trial], *, correct: bool = True
) -> dict[tuple[str, str], dict[str, Any]]:
    """The proposal each add or decoy trial deserves, right or wrong; controls stay silent."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for trial in trials:
        if trial.row.candidates:
            wrong = next(c for c in trial.row.candidates if c != trial.truth)
            out[trial.key] = _proposal(
                RefinementKind.RESOLVE_AMBIGUOUS,
                src=None,
                node_id=trial.row.node_id,
                name=trial.row.name,
                candidate=trial.truth if correct else wrong,
            )
        elif trial.truth is not None and trial.row.definers:
            out[trial.key] = _proposal(
                RefinementKind.ADD_EDGE,
                src=trial.row.node_id,
                name=trial.row.name,
                dst=trial.truth if correct else WRONG_DST,
            )
    return out


#: a destination no trial's truth ever is, so a wrong answer is unambiguously wrong
WRONG_DST = "lib.py::direct_target"


def _tuned(service: RefinementService, **tuning: Any) -> RefinementService:
    """The same service under a different `min_precision`, which is what a gate test varies."""
    return _reconfigured(service, tuning=tuning)


def _reconfigured(
    service: RefinementService,
    *,
    tuning: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
) -> RefinementService:
    """The same service over one changed observer sub-model, which is what a gate test varies."""
    observer = service.user.observer
    changed: dict[str, Any] = {}
    if tuning:
        changed["tuning"] = observer.tuning.model_copy(update=dict(tuning))
    if budget:
        changed["budget"] = observer.budget.model_copy(update=dict(budget))
    return RefinementService(
        service.index,
        service.root,
        service.settings,
        service.user.model_copy(
            update={"observer": observer.model_copy(update=changed)}
        ),
        registry=RunRegistry(),
    )


def _run(
    service: RefinementService,
    answers: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    size: int = 2,
    seed: int = 1,
    pretend: FakeRun | None = None,
    on_plan: Any = None,
) -> EvalRun:
    """One eval over this service, answered by a script rather than an SDK."""
    return EvalRun(
        service=service,
        build=eval_build(answers, pretend),
        runner=RunnerKind.FAKE,
        model="haiku",
        size=size,
        seed=seed,
        on_plan=on_plan,
    )


async def _evaluate(
    service: RefinementService,
    *,
    suite: EvalSuite = EvalSuite.ADD,
    correct: bool = True,
    size: int = 2,
    seed: int = 1,
    pretend: FakeRun | None = None,
):
    """Run one suite with a script derived from the trials it will draw."""
    population = await Population.of(service.facts)
    trials = population.sample(suite=suite, size=size, seed=seed)
    return await _run(
        service,
        _answers(trials, correct=correct),
        size=size,
        seed=seed,
        pretend=pretend,
    ).report([suite])


async def test_a_perfect_add_run_measures_every_stratum(eval_service):
    report = await _evaluate(eval_service)
    by_key = {key_of(t.suite, t.stratum): t for t in report.suites}
    assert set(by_key) == {"add/same-module", "add/direct-import", "add/neither"}
    assert all(t.correct == t.n and t.wrong == 0 for t in by_key.values())
    assert by_key["add/same-module"].metrics.lower_bound_95 == pytest.approx(
        wilson_lower(2, 2)
    )


async def test_the_rows_a_run_writes_are_what_the_gate_reads_back(eval_service):
    await _evaluate(eval_service)
    rows = await eval_service.index.evals.latest(RunnerKind.FAKE, "haiku")
    assert {(row.suite, row.stratum) for row in rows} == {
        ("add", Stratum.SAME_MODULE),
        ("add", Stratum.DIRECT_IMPORT),
        ("add", Stratum.NEITHER),
    }
    assert all(row.metrics.false_removal_rate == 0.0 for row in rows)


async def test_a_perfect_short_run_cannot_clear_the_default_bar(eval_service):
    report = await _evaluate(eval_service)
    assert report.activation.proven == ()
    assert report.min_precision == 0.95
    assert any("below the 73" in line for line in report.notes.unprovable_drawn)


async def test_the_same_perfect_run_proves_the_strata_at_a_lower_bar(eval_service):
    """`flawless_floor(0.2)` is 1, so even this package's one-truth strata clear it."""
    report = await _evaluate(_tuned(eval_service, min_precision=0.2))
    assert set(report.activation.proven) == {
        "add/same-module",
        "add/direct-import",
        "add/neither",
    }
    assert report.notes.unprovable_drawn == ()
    assert report.notes.unprovable_judged == ()


async def test_a_full_draw_judged_on_too_few_trials_is_still_unprovable(eval_service):
    """A Wilson bound reads `correct + wrong`, so a stratum the runner ignored proves nothing."""
    report = await _run(_tuned(eval_service, min_precision=0.2), {}).report(
        [EvalSuite.ADD]
    )
    assert report.notes.unprovable_drawn == ()
    assert {line.split(":")[0] for line in report.notes.unprovable_judged} == {
        "add/same-module",
        "add/direct-import",
        "add/neither",
    }
    assert all("judged 0 trials" in line for line in report.notes.unprovable_judged)


async def test_a_regression_un_proves_a_stratum_through_the_policy(eval_service):
    """P1 end to end: the newest row governs, so a failing eval takes activation back."""
    tuned = _tuned(eval_service, min_precision=0.2)
    assert "add/same-module" in (await _evaluate(tuned)).activation.proven
    after = await _evaluate(tuned, correct=False)
    assert "add/same-module" not in after.activation.proven
    policy = TierPolicy.of(
        await eval_service.index.evals.latest(RunnerKind.FAKE, "haiku"),
        min_precision=0.2,
        runner=RunnerKind.FAKE,
        model="haiku",
    )
    assert ("add", Stratum.SAME_MODULE) in policy.measured
    assert ("add", Stratum.SAME_MODULE) not in policy.proven


@pytest.mark.parametrize("suite", ALL_SUITES)
async def test_an_eval_stores_no_refinement_row(eval_service, suite):
    """Invariant 2: the judge answers `propose`, so the ledger never sees an eval's proposals."""
    await _evaluate(eval_service, suite=suite)
    assert await eval_service.index.refinements.refinements() == []


async def test_every_batch_leaves_one_eval_run_row(eval_service):
    report = await _evaluate(eval_service)
    rows = await eval_service.index.runs.runs()
    assert len(rows) == report.runs == report.plan.runs_planned == 3
    assert all(row.trigger_kind is TriggerKind.EVAL for row in rows)
    assert all(row.status is RunStatus.SUCCEEDED for row in rows)


async def test_the_brief_a_batch_is_given_holds_its_trials_and_nothing_else(
    eval_service,
):
    """H3: a short batch filled from the index would spend turns on rows no trial can score."""
    await _evaluate(eval_service, size=1)
    rows = await eval_service.index.runs.runs()
    assert rows
    for row in rows:
        assert "targets: 1 of 1 queue rows" in row.prompt


async def test_a_control_run_that_proposes_nothing_is_clean(eval_service):
    report = await _evaluate(eval_service, suite=EvalSuite.COLLISION, size=5)
    (got,) = report.suites
    assert (got.n, got.false_adds) == (1, 0)
    assert got.metrics.false_add_rate == 0.0
    assert "collision/all" in report.activation.proven


async def test_a_collision_batch_actually_briefs_its_rows(eval_service):
    """The first dogfood briefed nothing here: the model answered "no unresolved rows" and the
    control cleared its gate without ever being asked a question."""
    await _evaluate(eval_service, suite=EvalSuite.COLLISION, size=5)
    (row,) = await eval_service.index.runs.runs()
    assert "targets: 1 of 1 queue rows" in row.prompt
    assert "match" in row.prompt


async def test_a_control_run_that_adds_an_edge_fails_its_gate(eval_service):
    population = await Population.of(eval_service.facts)
    (trial,) = population.sample(suite=EvalSuite.COLLISION, size=5, seed=1)
    answers = {
        trial.key: _proposal(
            RefinementKind.ADD_EDGE,
            src=trial.row.node_id,
            name=trial.row.name,
            dst="other.py::match",
        )
    }
    report = await _run(eval_service, answers, size=5).report([EvalSuite.COLLISION])
    (got,) = report.suites
    assert got.false_adds == 1
    assert report.activation.proven == ()


async def test_a_run_that_aborts_stops_its_suite_and_the_report_says_so(eval_service):
    report = await _evaluate(
        eval_service, size=1, pretend=FakeRun(stop="the model gave up")
    )
    assert report.suites == ()
    assert report.runs == 0
    assert any("aborted" in line for line in report.notes.stopped)
    assert await eval_service.index.evals.latest(RunnerKind.FAKE, "haiku") == []


async def test_a_run_that_aborts_still_reports_what_it_cost(eval_service):
    """The dogfood spent $0.41 and the report said $0.10: an aborted run's money is still gone."""
    report = await _evaluate(
        eval_service,
        size=1,
        pretend=FakeRun(
            stop="the model gave up", usage=RunUsage(cost_usd=0.25, num_turns=9)
        ),
    )
    assert report.suites == ()
    assert report.runs == 0
    assert report.cost_usd == pytest.approx(0.25)
    assert any("$0.2500" in line for line in report.notes.stopped)


async def test_an_aborted_re_run_leaves_the_earlier_measurement_standing(eval_service):
    """An abort is not a measurement, so it must not overwrite the row that was one."""
    tuned = _tuned(eval_service, min_precision=0.2)
    assert "add/same-module" in (await _evaluate(tuned)).activation.proven
    after = await _evaluate(tuned, pretend=FakeRun(stop="the model gave up"))
    assert after.suites == ()
    assert any("no row is written" in line for line in after.notes.stopped)
    policy = TierPolicy.of(
        await eval_service.index.evals.latest(RunnerKind.FAKE, "haiku"),
        min_precision=0.2,
        runner=RunnerKind.FAKE,
        model="haiku",
    )
    assert ("add", Stratum.SAME_MODULE) in policy.proven


async def test_the_reported_cost_covers_every_run_not_only_the_measured_ones(
    eval_service,
):
    report = await _evaluate(
        eval_service, pretend=FakeRun(usage=RunUsage(cost_usd=0.1, num_turns=4))
    )
    assert report.runs == 3
    assert report.cost_usd == pytest.approx(0.3)
    assert sum(got.spend.cost_usd for got in report.suites) == pytest.approx(0.3)


async def test_a_short_draw_and_an_empty_stratum_are_both_named(eval_service):
    report = await _evaluate(eval_service, size=3)
    assert any(
        line.startswith("add/direct-import: drew 1 of 3") for line in report.notes.short
    )
    assert report.notes.empty == ()


async def test_a_suite_with_nothing_to_draw_reports_it_empty(refine_service):
    """`refine_service`'s package has no externally bound queue row to collide over."""
    report = await _run(refine_service, {}, size=4).report([EvalSuite.COLLISION])
    assert report.notes.empty == ("collision/all",)
    assert report.suites == ()


async def test_the_report_carries_what_it_was_asked_for(eval_service):
    report = await _evaluate(
        eval_service, size=2, seed=9, pretend=FakeRun(usage=RunUsage(cost_usd=0.05))
    )
    assert (report.runner, report.model) == (RunnerKind.FAKE, "haiku")
    assert (report.plan.sample, report.plan.seed) == (2, 9)
    assert report.cost_usd == pytest.approx(0.15)
    assert sum(t.spend.cost_usd for t in report.suites) == pytest.approx(0.15)


# ---------------------------------------------------------------- the plan and the ceilings


async def test_a_dry_run_answers_with_the_plan_and_opens_nothing(eval_service):
    seen: list[EvalPlan] = []
    report = await _run(eval_service, {}, on_plan=seen.append).report(
        list(ALL_SUITES), dry_run=True
    )
    assert report.plan.runs_planned == 6
    assert report.plan.suites == tuple(suite.value for suite in ALL_SUITES)
    assert "add/same-module: 2 trials" in report.plan.strata
    assert report.plan.max_budget_usd_per_eval == 12.0
    assert (report.suites, report.runs, report.cost_usd) == ((), 0, 0.0)
    assert await eval_service.index.runs.runs() == []
    assert [plan.runs_planned for plan in seen] == [6]


async def test_the_plan_reaches_its_reader_before_the_first_run_opens(eval_service):
    """The cost guard is only a guard if it is read while there is still money to save."""
    opened: list[str] = []
    factory = eval_build({})
    at_plan: list[int] = []

    def build(service: RefinementService, proposer: Any) -> Any:
        opened.append(service.identity)
        return factory(service, proposer)

    run = EvalRun(
        service=eval_service,
        build=build,
        runner=RunnerKind.FAKE,
        model="haiku",
        size=1,
        seed=1,
        on_plan=lambda _plan: at_plan.append(len(opened)),
    )
    report = await run.report([EvalSuite.ADD])
    assert at_plan == [0]
    assert len(opened) == report.plan.runs_planned == 3


def test_the_default_eval_ceiling_covers_a_default_suite_all_plan():
    """A ceiling under its own default plan stops `auditr graph eval --suite all` a fifth of the
    way through and exits 1 on every repo, which is the one invocation four surfaces name."""
    budget, limits = BudgetConfig(), LimitsConfig()
    full = [_add_trial(src=f"a.py::f{i}") for i in range(EVAL_SAMPLE_DEFAULT)]
    strata = sum(len(EvalSuiteSpec.of(suite).STRATA) for suite in ALL_SUITES)
    planned = strata * len(batches(full, limits.max_nodes_per_run))
    assert budget.max_budget_usd_per_eval >= planned * budget.max_budget_usd_per_run


@pytest.mark.parametrize(("ceiling", "measured"), [(0.1, 0), (0.4, 2)])
async def test_the_eval_ceiling_stops_the_next_run_before_it_opens(
    eval_service, ceiling, measured
):
    """The second case is what pins the running total: with `spent` never accumulating, the third
    run reads the same $0.00 the first one did and every stratum measures."""
    tight = _reconfigured(eval_service, budget={"max_budget_usd_per_eval": ceiling})
    report = await _evaluate(
        tight, pretend=FakeRun(usage=RunUsage(cost_usd=0.1, num_turns=1))
    )
    assert report.runs == measured
    assert len(report.suites) == measured
    assert any("stopped: budget" in line for line in report.notes.stopped)
    assert len(await eval_service.index.runs.runs()) == measured


async def _unbriefed(
    service: RefinementService, monkeypatch: pytest.MonkeyPatch
) -> EvalReport:
    """One collision batch whose row reaches no brief, which is the collision bug's own shape."""
    real = await Population.of(service.facts)
    ghost = UnresolvedRow(
        node_id="gone.py::vanished",
        fact_kind=FactKind.CALLEE,
        name="q",
        reason="unimportable_name",
        call_form=CallForm.BARE,
        externally_bound=True,
    )
    planted = real.model_copy(update={"collisions": (ghost,)})

    async def _fixed(_cls: Any, _facts: Any) -> Population:
        return planted

    monkeypatch.setattr(Population, "of", classmethod(_fixed))
    return await _run(service, {}).report([EvalSuite.COLLISION])


async def test_a_batch_that_briefs_fewer_trials_than_it_holds_measures_nothing(
    eval_service, monkeypatch
):
    """A control cannot clear a gate over questions nobody was asked."""
    report = await _unbriefed(eval_service, monkeypatch)
    assert report.suites == ()
    assert report.activation.proven == ()
    assert any("unbriefed" in line for line in report.notes.stopped)
    assert await eval_service.index.evals.latest(RunnerKind.FAKE, "haiku") == []


async def test_the_unbriefed_line_says_what_the_stored_row_says(
    eval_service, monkeypatch
):
    """The line used to call the run `failed` while `auditr graph log` showed it succeeded: one
    run, two statuses, and the reader has to guess which surface is lying."""
    report = await _unbriefed(eval_service, monkeypatch)
    (row,) = await eval_service.index.runs.runs()
    (line,) = [note for note in report.notes.stopped if "unbriefed" in note]
    assert f"the run ended {row.status.value} unbriefed" in line
    assert row.status is RunStatus.SUCCEEDED
    assert "nothing it did is a measurement" in line and "no row is written" in line


# ---------------------------------------------------------------- the eval brief's isolation


async def _pinned_row(service: RefinementService) -> int:
    """One stored correction this checkout's own brief would warn about."""
    run = await service.begin()
    return await service.index.refinements.add_refinement(
        Refinement(
            run_id=run.run_id,
            repo_identity=service.identity,
            kind=RefinementKind.ADD_EDGE,
            reason="it resolves there",
            target=RefinementTarget(
                src="m.py::calls_same",
                dst="other.py::match",
                edge_kind=EdgeKind.CALLS,
                name="match",
            ),
            tier=Tier.C,
            status=RefinementStatus.PINNED,
            drifted=True,
        )
    )


async def test_a_reader_holding_synthetic_rows_reports_no_stale_correction(
    eval_service,
):
    """P5: an eval brief must show this checkout's ledger history no more than its queue."""
    await _pinned_row(eval_service)
    assert await eval_service.facts.stale("") != ()
    masked = eval_service.facts.model_copy(
        update={
            "synthetic": (
                UnresolvedRow(
                    node_id="m.py::calls_same",
                    fact_kind=FactKind.CALLEE,
                    name="same_target",
                    reason="unimportable_name",
                    call_form=CallForm.BARE,
                    definers=("m.py::same_target",),
                ),
            )
        }
    )
    assert await masked.stale("") == ()


async def test_an_eval_brief_never_carries_a_pinned_correction(eval_service):
    await _pinned_row(eval_service)
    await _evaluate(eval_service, size=1)
    rows = await eval_service.index.runs.runs()
    evals = [row for row in rows if row.trigger_kind is TriggerKind.EVAL]
    assert evals
    assert all("other.py::match" not in (row.prompt or "") for row in evals)
