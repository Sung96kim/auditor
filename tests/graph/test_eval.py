"""The eval suites: the population they draw from, the judge, the arithmetic and the rows."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from graph._support import eval_build

from auditor.cli.helpers import load_settings, load_user, open_index
from auditor.database import IndexStore
from auditor.graph.model import CallForm, EdgeKind, FactKind, UnresolvedRow
from auditor.graph.refine.brief import BriefTarget
from auditor.graph.refine.eval import (
    DECOY_COUNT,
    JUDGED,
    Judge,
    Judgement,
    Population,
    SuiteSpend,
    Trial,
    Truth,
    _without_multi_target,
    batches,
    key_of,
    run_eval,
    sample,
    tally,
)
from auditor.graph.refine.models import (
    ALL_SUITES,
    CONTROL_STRATUM,
    EvalSuite,
    Proposal,
    ProposalOutcome,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    RunnerKind,
    RunStatus,
    RunUsage,
    Stratum,
    Tier,
    TriggerKind,
    flawless_floor,
    wilson_lower,
)
from auditor.graph.refine.service import (
    RefinementRefused,
    RefinementService,
    RunRegistry,
)
from auditor.graph.refine.tiers import TierPolicy

#: the four truths `graph_repo_eval` is built to yield, exactly (P6)
EXPECTED_TRUTHS = {
    ("m.py::calls_same", "m.py::same_target", "bare", "same-module"),
    ("m.py::calls_direct", "lib.py::direct_target", "bare", "direct-import"),
    ("m.py::calls_reexported", "pkg/deep.py::reexported", "bare", "neither"),
    ("m.py::Holder.uses", "m.py::Holder.helper", "self", "same-module"),
}
#: the sites the ground truth must leave out, each for its own reason
EXCLUDED_SOURCES = (
    "m.py::attribute_call",  # an attribute call carries no bounded form
    "m.py::binds_then_calls",  # the node binds the name itself, so `form_for` drops it
    "m.py::calls_test_only",  # its only definer is test code, so it is not a definer at all
    "m.py::externally_bound_call",  # the receiver is aliased from a non-repo import
)


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


# ---------------------------------------------------------------- the population


async def test_the_ground_truth_is_every_bounded_single_definer_call(population):
    got = {
        (t.src, t.dst, t.call_form.value, t.stratum.value) for t in population.truths
    }
    assert got == EXPECTED_TRUTHS


@pytest.mark.parametrize("src", EXCLUDED_SOURCES)
async def test_a_site_the_tier_b_shape_excludes_is_not_a_truth(population, src):
    assert all(truth.src != src for truth in population.truths)


async def test_the_strata_are_counted_as_the_package_lays_them_out(population):
    assert population.counts_by_stratum() == {
        "same-module": 2,
        "direct-import": 1,
        "neither": 1,
    }


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


def test_a_site_that_resolves_one_name_twice_is_not_a_truth():
    """No such site exists on this repo or in the fixture, so the rule is proven here or nowhere."""
    twice = (_truth(dst="a.py::g"), _truth(dst="b.py::g"), _truth(src="a.py::h"))
    assert [t.src for t in _without_multi_target(twice)] == ["a.py::h"]


# ---------------------------------------------------------------- sampling


@pytest.mark.parametrize("suite", ALL_SUITES)
async def test_the_same_seed_draws_the_same_trials(population, suite):
    first = sample(population, suite=suite, size=2, seed=7)
    assert first == sample(population, suite=suite, size=2, seed=7)


async def test_a_different_seed_reorders_the_draw(population):
    """Four truths and a draw of one: a different seed has to be able to pick a different one."""
    seeds = {
        sample(population, suite=EvalSuite.ADD, size=1, seed=seed)[0].truth
        for seed in range(12)
    }
    assert len(seeds) > 1


async def test_a_stratum_draws_no_more_than_it_has(population):
    trials = sample(population, suite=EvalSuite.ADD, size=50, seed=1)
    assert len(trials) == len(population.truths)


async def test_the_add_suite_draws_per_stratum(population):
    trials = sample(population, suite=EvalSuite.ADD, size=1, seed=1)
    assert sorted(str(t.stratum) for t in trials) == [
        "direct-import",
        "neither",
        "same-module",
    ]


async def test_every_add_trial_looks_like_the_row_the_queue_would_have_written(
    population,
):
    for trial in sample(population, suite=EvalSuite.ADD, size=50, seed=1):
        assert trial.row.definers == (trial.truth,)
        assert trial.row.call_form in (CallForm.BARE, CallForm.SELF)
        assert trial.row.reason.value == "unimportable_name"
        assert trial.stratum is not CONTROL_STRATUM


async def test_a_decoy_trial_offers_the_truth_once_and_never_add_edge(
    population,
):
    for trial in sample(population, suite=EvalSuite.DECOY, size=50, seed=3):
        assert trial.row.candidates.count(trial.truth) == 1
        assert 1 < len(trial.row.candidates) <= DECOY_COUNT + 1
        assert trial.row.definers == ()
        allowed = BriefTarget.of(trial.row, path="m.py", line=1, facts=()).allowed
        assert RefinementKind.ADD_EDGE not in allowed
        assert RefinementKind.RESOLVE_AMBIGUOUS in allowed
        assert str(trial.stratum) == CONTROL_STRATUM


async def test_a_negative_trial_names_nothing_this_repo_defines(population):
    trials = sample(population, suite=EvalSuite.NEGATIVE, size=4, seed=5)
    assert trials
    for trial in trials:
        assert trial.row.name not in population.names_defined
        assert trial.truth is None
        assert BriefTarget.of(trial.row, path="m.py", line=1, facts=()).allowed == (
            RefinementKind.ANNOTATE_NODE,
            RefinementKind.UNRESOLVABLE,
        )


async def test_a_collision_trial_is_a_real_queue_row(population):
    (trial,) = sample(population, suite=EvalSuite.COLLISION, size=9, seed=1)
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


async def test_a_readable_but_illegal_payload_is_rejected_and_recorded():
    """`Proposal.read`'s lenient arm: the model sees the shape a real run would answer with."""
    judge = Judge.over([_add_trial()])
    verdict = await judge.propose(
        "run-1", {"kind": "add_edge", "target": {}, "reason": ""}
    )
    assert verdict.outcome is ProposalOutcome.REJECTED
    assert verdict.kind is RefinementKind.ADD_EDGE
    assert judge.off_target


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
        spend={"same-module": SuiteSpend(cost_usd=0.25, num_turns=9, runs=2)},
    )
    assert (got.n, got.correct, got.wrong, got.missed) == (4, 2, 1, 1)
    assert got.metrics.precision == pytest.approx(2 / 3)
    assert got.metrics.recall == pytest.approx(0.5)
    assert got.metrics.false_add_rate == pytest.approx(0.25)
    assert got.metrics.lower_bound_95 == pytest.approx(wilson_lower(2, 3))
    assert (got.cost_usd, got.num_turns, got.runs) == (0.25, 9, 2)


def test_a_tally_that_judged_nothing_but_misses_divides_by_zero_safely():
    (got,) = tally(_judgements("missed", "missed"), suite=EvalSuite.ADD, spend={})
    assert (got.metrics.precision, got.metrics.lower_bound_95) == (0.0, 0.0)
    assert got.metrics.n == 2


def test_a_control_tally_counts_false_adds_into_the_rate():
    (got,) = tally(
        [
            Judgement(
                trial=_add_trial().model_copy(
                    update={"suite": EvalSuite.COLLISION, "stratum": CONTROL_STRATUM}
                ),
                verdict=verdict,
            )
            for verdict in ("clean", "false_add")
        ],
        suite=EvalSuite.COLLISION,
        spend={},
    )
    assert (got.false_adds, got.n) == (1, 2)
    assert got.metrics.false_add_rate == pytest.approx(0.5)


def test_the_tally_keeps_the_strata_apart():
    got = tally(
        _judgements("correct", stratum=Stratum.SAME_MODULE)
        + _judgements("wrong", stratum=Stratum.NEITHER),
        suite=EvalSuite.ADD,
        spend={},
    )
    assert {str(t.stratum): t.n for t in got} == {"same-module": 1, "neither": 1}


def test_the_row_carries_every_column_the_gate_reads():
    (got,) = tally(_judgements("correct"), suite=EvalSuite.ADD, spend={})
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
        spend={"same-module": SuiteSpend(cost_usd=0.4, num_turns=3, runs=1)},
    )
    (opened,) = tally(_judgements("correct"), suite=EvalSuite.ADD, spend={})
    assert closed.cost_usd == 0.4
    assert opened.cost_usd == 0.0


def test_the_report_key_names_the_suite_and_the_stratum():
    assert key_of("add", Stratum.NEITHER) == "add/neither"
    assert key_of("collision", CONTROL_STRATUM) == "collision/all"


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
    user = service.user
    return RefinementService(
        service.index,
        service.root,
        service.settings,
        user.model_copy(
            update={
                "observer": user.observer.model_copy(
                    update={"tuning": user.observer.tuning.model_copy(update=tuning)}
                )
            }
        ),
        registry=RunRegistry(),
    )


async def _evaluate(
    service: RefinementService,
    *,
    suite: EvalSuite = EvalSuite.ADD,
    correct: bool = True,
    size: int = 2,
    seed: int = 1,
    **kwargs: Any,
):
    """Run one suite with a script derived from the trials it will draw."""
    population = await Population.of(service.facts)
    trials = sample(population, suite=suite, size=size, seed=seed)
    return await run_eval(
        service,
        build=eval_build(_answers(trials, correct=correct), **kwargs),
        runner=RunnerKind.FAKE,
        model="haiku",
        suites=[suite],
        size=size,
        seed=seed,
    )


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
    assert {(row.suite, str(row.stratum)) for row in rows} == {
        ("add", "same-module"),
        ("add", "direct-import"),
        ("add", "neither"),
    }
    assert all(row.metrics.false_removal_rate == 0.0 for row in rows)


async def test_a_perfect_short_run_cannot_clear_the_default_bar(eval_service):
    report = await _evaluate(eval_service)
    assert report.proven == ()
    assert report.min_precision == 0.95
    assert any("below the 73" in line for line in report.unprovable)


async def test_the_same_perfect_run_proves_the_strata_at_a_lower_bar(eval_service):
    """`flawless_floor(0.2)` is 1, so even this package's one-truth strata clear it."""
    report = await _evaluate(_tuned(eval_service, min_precision=0.2))
    assert set(report.proven) == {
        "add/same-module",
        "add/direct-import",
        "add/neither",
    }
    assert report.unprovable == ()


async def test_a_regression_un_proves_a_stratum_through_the_policy(eval_service):
    """P1 end to end: the newest row governs, so a failing eval takes activation back."""
    tuned = _tuned(eval_service, min_precision=0.2)
    assert "add/same-module" in (await _evaluate(tuned)).proven
    after = await _evaluate(tuned, correct=False)
    assert "add/same-module" not in after.proven
    policy = TierPolicy.of(
        await eval_service.index.evals.latest(RunnerKind.FAKE, "haiku"),
        min_precision=0.2,
        runner=RunnerKind.FAKE,
        model="haiku",
    )
    assert ("add", "same-module") in policy.measured
    assert ("add", "same-module") not in policy.proven


@pytest.mark.parametrize("suite", ALL_SUITES)
async def test_an_eval_stores_no_refinement_row(eval_service, suite):
    """Invariant 2: the judge answers `propose`, so the ledger never sees an eval's proposals."""
    await _evaluate(eval_service, suite=suite)
    assert await eval_service.index.refinements.refinements() == []


async def test_every_batch_leaves_one_eval_run_row(eval_service):
    report = await _evaluate(eval_service)
    rows = await eval_service.index.runs.runs()
    assert len(rows) == report.runs == report.runs_planned == 3
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
    assert "collision/all" in report.proven


async def test_a_collision_batch_actually_briefs_its_rows(eval_service):
    """The first dogfood briefed nothing here: the model answered "no unresolved rows" and the
    control cleared its gate without ever being asked a question."""
    await _evaluate(eval_service, suite=EvalSuite.COLLISION, size=5)
    (row,) = await eval_service.index.runs.runs()
    assert "targets: 1 of 1 queue rows" in row.prompt
    assert "match" in row.prompt


async def test_a_control_run_that_adds_an_edge_fails_its_gate(eval_service):
    population = await Population.of(eval_service.facts)
    (trial,) = sample(population, suite=EvalSuite.COLLISION, size=5, seed=1)
    answers = {
        trial.key: _proposal(
            RefinementKind.ADD_EDGE,
            src=trial.row.node_id,
            name=trial.row.name,
            dst="other.py::match",
        )
    }
    report = await run_eval(
        eval_service,
        build=eval_build(answers),
        runner=RunnerKind.FAKE,
        model="haiku",
        suites=[EvalSuite.COLLISION],
        size=5,
        seed=1,
    )
    (got,) = report.suites
    assert got.false_adds == 1
    assert report.proven == ()


async def test_a_run_that_aborts_stops_its_suite_and_the_report_says_so(eval_service):
    report = await _evaluate(eval_service, size=1, stop="the model gave up")
    assert report.suites == ()
    assert report.runs == 0
    assert any("aborted" in line for line in report.short)
    assert await eval_service.index.evals.latest(RunnerKind.FAKE, "haiku") == []


async def test_a_run_that_aborts_still_reports_what_it_cost(eval_service):
    """The dogfood spent $0.41 and the report said $0.10: an aborted run's money is still gone."""
    report = await _evaluate(
        eval_service,
        size=1,
        stop="the model gave up",
        usage=RunUsage(cost_usd=0.25, num_turns=9),
    )
    assert report.suites == ()
    assert report.runs == 0
    assert report.cost_usd == pytest.approx(0.25)
    assert any("$0.2500" in line for line in report.short)


async def test_the_reported_cost_covers_every_run_not_only_the_measured_ones(
    eval_service,
):
    report = await _evaluate(eval_service, usage=RunUsage(cost_usd=0.1, num_turns=4))
    assert report.runs == 3
    assert report.cost_usd == pytest.approx(0.3)
    assert sum(got.cost_usd for got in report.suites) == pytest.approx(0.3)


async def test_a_short_draw_and_an_empty_stratum_are_both_named(eval_service):
    report = await _evaluate(eval_service, size=3)
    assert any(
        line.startswith("add/direct-import: drew 1 of 3") for line in report.short
    )
    assert report.empty == ()


async def test_a_suite_with_nothing_to_draw_reports_it_empty(refine_service):
    """`refine_service`'s package has no externally bound queue row to collide over."""
    report = await run_eval(
        refine_service,
        build=eval_build({}),
        runner=RunnerKind.FAKE,
        model="haiku",
        suites=[EvalSuite.COLLISION],
        size=4,
        seed=1,
    )
    assert report.empty == ("collision/all",)
    assert report.suites == ()


async def test_the_report_carries_what_it_was_asked_for(eval_service):
    report = await _evaluate(eval_service, size=2, seed=9)
    assert (report.runner, report.model) == (RunnerKind.FAKE, "haiku")
    assert (report.sample, report.seed) == (2, 9)
    assert report.cost_usd >= sum(t.cost_usd for t in report.suites)
