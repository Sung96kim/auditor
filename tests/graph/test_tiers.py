"""Spec 9.2's tier column and spec 10.3's activation table."""

import pytest

from auditor.graph.model import CallForm, EdgeKind, UnresolvedRow
from auditor.graph.refine.models import (
    CONTROL_STRATUM,
    EvalMetrics,
    EvalRow,
    EvalStratum,
    Proposal,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    RunnerKind,
    Stratum,
    Tier,
)
from auditor.graph.refine.tiers import TierPolicy


def _add_edge() -> Proposal:
    return Proposal(
        kind=RefinementKind.ADD_EDGE,
        target=RefinementTarget(
            src="a.py::f", dst="b.py::g", edge_kind=EdgeKind.CALLS, name="g"
        ),
        reason="the bare call resolves in b.py",
    )


def _row(**kw) -> UnresolvedRow:
    return UnresolvedRow(
        node_id="a.py::f",
        fact_kind="callee",
        name="g",
        reason="unimportable_name",
        definers=kw.pop("definers", ("b.py::g",)),
        **kw,
    )


def _eval(
    suite: str,
    *,
    lower: float = 1.0,
    false_adds: float = 0.0,
    stratum: EvalStratum | None = None,
    n: int = 80,
) -> EvalRow:
    """One stored row; every suite but `add` is stored under the one control stratum (P2)."""
    return EvalRow(
        repo_identity="/repo/.git",
        runner=RunnerKind.CLAUDE,
        model="haiku",
        suite=suite,
        stratum=stratum or (Stratum.SAME_MODULE if suite == "add" else CONTROL_STRATUM),
        metrics=EvalMetrics(n=n, lower_bound_95=lower, false_add_rate=false_adds),
    )


def _proven(*rows: EvalRow) -> TierPolicy:
    return TierPolicy.of(
        rows, min_precision=0.95, runner=RunnerKind.CLAUDE, model="haiku"
    )


@pytest.mark.parametrize(
    ("kind", "target", "payload", "tier"),
    [
        (
            RefinementKind.CONFIRM_EDGE,
            RefinementTarget(
                src="a.py::f", dst="b.py::g", edge_kind=EdgeKind.CALLS, name="g"
            ),
            RefinementPayload(),
            Tier.A,
        ),
        (
            RefinementKind.ANNOTATE_NODE,
            RefinementTarget(node_id="a.py::f"),
            RefinementPayload(annotation="the retry path"),
            Tier.A,
        ),
        (
            RefinementKind.RELABEL_CLUSTER,
            RefinementTarget(members=("a.py::f",)),
            RefinementPayload(label="retry"),
            Tier.A,
        ),
        (
            RefinementKind.UNRESOLVABLE,
            RefinementTarget(node_id="a.py::f", name="g"),
            RefinementPayload(reason_code="dynamic"),
            Tier.A,
        ),
        (
            RefinementKind.RESOLVE_AMBIGUOUS,
            RefinementTarget(node_id="a.py::f", name="g", edge_kind=EdgeKind.CALLS),
            RefinementPayload(candidate="b.py::g"),
            Tier.A,
        ),
        (
            RefinementKind.MOVE_NODE,
            RefinementTarget(node_id="a.py::f", members=("b.py::g",)),
            RefinementPayload(),
            Tier.C,
        ),
        (
            RefinementKind.RETARGET_EDGE,
            RefinementTarget(
                src="a.py::f",
                from_dst="b.py::g",
                to_dst="c.py::g",
                edge_kind=EdgeKind.CALLS,
                name="g",
            ),
            RefinementPayload(),
            Tier.C,
        ),
    ],
)
def test_the_kind_decides_every_tier_but_add_edge(kind, target, payload, tier):
    proposal = Proposal(
        kind=kind, target=target, payload=payload, reason="judged by hand"
    )
    assert TierPolicy().tier(proposal, row=None, verified=True) is tier


@pytest.mark.parametrize(
    ("call_form", "definers", "external", "verified", "tier"),
    [
        (CallForm.BARE, ("b.py::g",), False, True, Tier.B),
        (CallForm.SELF, ("b.py::g",), False, True, Tier.B),
        (CallForm.ATTR, ("b.py::g",), False, True, Tier.C),
        (CallForm.BARE, ("b.py::g", "c.py::g"), False, True, Tier.C),
        (CallForm.BARE, ("b.py::g",), True, True, Tier.C),
        (CallForm.BARE, ("b.py::g",), False, False, Tier.C),
    ],
)
def test_add_edge_reaches_tier_b_only_in_the_bounded_shape(
    call_form, definers, external, verified, tier
):
    row = _row(call_form=call_form, definers=definers, externally_bound=external)
    assert TierPolicy().tier(_add_edge(), row=row, verified=verified) is tier


def test_an_edge_proposal_with_no_queue_row_is_tier_c():
    assert TierPolicy().tier(_add_edge(), row=None, verified=True) is Tier.C


@pytest.mark.parametrize(
    "kind",
    [
        RefinementKind.CONFIRM_EDGE,
        RefinementKind.ANNOTATE_NODE,
        RefinementKind.RELABEL_CLUSTER,
        RefinementKind.UNRESOLVABLE,
    ],
)
def test_the_four_safe_kinds_go_active_with_no_eval_row(kind):
    assert TierPolicy().status(kind, Tier.A) is RefinementStatus.ACTIVE


def test_resolve_ambiguous_stays_pending_until_the_decoy_suite_clears():
    assert (
        TierPolicy().status(RefinementKind.RESOLVE_AMBIGUOUS, Tier.A)
        is RefinementStatus.PENDING
    )
    assert (
        _proven(_eval("decoy")).status(RefinementKind.RESOLVE_AMBIGUOUS, Tier.A)
        is RefinementStatus.ACTIVE
    )


def test_tier_b_needs_both_the_add_suite_and_the_collision_control():
    add_only = _proven(_eval("add"))
    assert add_only.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.PENDING
    both = _proven(_eval("add"), _eval("collision", lower=0.0))
    assert both.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.ACTIVE


def test_a_collision_control_with_a_false_add_proves_nothing():
    policy = _proven(_eval("add"), _eval("collision", false_adds=0.01))
    assert policy.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.PENDING


def test_another_runners_eval_row_proves_nothing_here():
    rows = [_eval("add"), _eval("collision", lower=0.0)]
    policy = TierPolicy.of(
        rows, min_precision=0.95, runner=RunnerKind.CODEX, model="haiku"
    )
    assert policy.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.PENDING


def test_an_add_suite_below_the_precision_floor_proves_nothing():
    """The gate the whole tier rests on: at 0.90 measured against a 0.95 floor, nothing
    activates."""
    policy = _proven(_eval("add", lower=0.90), _eval("collision", lower=0.0))
    assert policy.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.PENDING


def test_a_suite_that_ran_no_trials_proves_nothing():
    """`false_add_rate == 0.0` is true of a control that never ran; spec 10.2's controls are
    counted trials, not defaults."""
    policy = _proven(_eval("add"), _eval("collision", lower=0.0, n=0))
    assert policy.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.PENDING


def test_tier_b_reads_the_add_stratum_that_matches_the_proposal():
    """Spec 10.2: tier B's gate is the lower bound of the stratum matching its shape, so a repo
    whose `neither` stratum measured 0.50 does not activate `neither`-shaped proposals."""
    policy = _proven(
        _eval("add"),
        _eval("add", lower=0.50, stratum=Stratum.NEITHER),
        _eval("collision", lower=0.0),
    )
    assert (
        policy.status(RefinementKind.ADD_EDGE, Tier.B, stratum=Stratum.SAME_MODULE)
        is RefinementStatus.ACTIVE
    )
    assert (
        policy.status(RefinementKind.ADD_EDGE, Tier.B, stratum=Stratum.NEITHER)
        is RefinementStatus.PENDING
    )


def test_an_unmeasured_stratum_never_activates():
    policy = _proven(_eval("add"), _eval("collision", lower=0.0))
    assert (
        policy.status(RefinementKind.ADD_EDGE, Tier.B, stratum=Stratum.DIRECT_IMPORT)
        is RefinementStatus.PENDING
    )


def test_a_caller_that_names_no_stratum_needs_every_measured_one():
    """Without the proposal's shape the conservative reading is the whole suite, so one weak
    stratum holds the gate shut."""
    weak = _proven(
        _eval("add"),
        _eval("add", lower=0.50, stratum=Stratum.NEITHER),
        _eval("collision", lower=0.0),
    )
    assert weak.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.PENDING
    strong = _proven(
        _eval("add"),
        _eval("add", stratum=Stratum.NEITHER),
        _eval("collision", lower=0.0),
    )
    assert strong.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.ACTIVE


@pytest.mark.parametrize(
    ("src", "dst", "imports", "stratum"),
    [
        ("a.py::f", "a.py::g", (), Stratum.SAME_MODULE),
        ("a.py::f", "b.py::g", ("b.py",), Stratum.DIRECT_IMPORT),
        ("a.py::f", "b.py::g", ("c.py",), Stratum.NEITHER),
    ],
)
def test_the_stratum_is_read_from_the_two_modules_and_the_import_between_them(
    src: str, dst: str, imports: tuple[str, ...], stratum: Stratum
):
    assert Stratum.of(src, dst, imports=imports) is stratum


def test_resolve_ambiguous_that_failed_the_fact_check_is_not_tier_a():
    """`resolve_ambiguous` has a verifier (spec 9.2), so discarding its verdict would activate an
    edge the fact check refused."""
    proposal = Proposal(
        kind=RefinementKind.RESOLVE_AMBIGUOUS,
        target=RefinementTarget(node_id="a.py::f", name="g", edge_kind=EdgeKind.CALLS),
        payload=RefinementPayload(candidate="b.py::g"),
        reason="judged by hand",
    )
    assert TierPolicy().tier(proposal, row=None, verified=False) is Tier.C
    assert TierPolicy().tier(proposal, row=None, verified=True) is Tier.A


def test_tier_c_is_always_pending():
    assert (
        TierPolicy().status(RefinementKind.ADD_EDGE, Tier.C) is RefinementStatus.PENDING
    )


def test_a_control_suite_is_read_from_its_one_stratum():
    """P2: controls are stored under `all`, which is what the gate finds without a stratum."""
    policy = _proven(_eval("add"), _eval("collision", lower=0.0), _eval("decoy"))
    assert ("collision", CONTROL_STRATUM) in policy.proven
    assert ("decoy", CONTROL_STRATUM) in policy.proven
    assert (
        policy.status(RefinementKind.ADD_EDGE, Tier.B, stratum=Stratum.SAME_MODULE)
        is RefinementStatus.ACTIVE
    )
    assert (
        policy.status(RefinementKind.RESOLVE_AMBIGUOUS, Tier.A)
        is RefinementStatus.ACTIVE
    )


def test_the_latest_row_governs_so_a_regression_un_proves_a_stratum():
    """P1: `of` is handed the newest row per key, so a failing eval takes activation back."""
    proving = _proven(_eval("add"), _eval("collision", lower=0.0))
    regressed = _proven(_eval("add", lower=0.50), _eval("collision", lower=0.0))
    stratum = Stratum.SAME_MODULE
    assert (
        proving.status(RefinementKind.ADD_EDGE, Tier.B, stratum=stratum)
        is RefinementStatus.ACTIVE
    )
    assert (
        regressed.status(RefinementKind.ADD_EDGE, Tier.B, stratum=stratum)
        is RefinementStatus.PENDING
    )


@pytest.mark.parametrize(
    ("suite", "lower", "false_adds", "clears"),
    [
        ("add", 1.0, 0.0, True),
        ("add", 0.90, 0.0, False),
        ("decoy", 1.0, 0.0, True),
        ("decoy", 0.90, 0.0, False),
        ("collision", 0.0, 0.0, True),
        ("collision", 1.0, 0.01, False),
        ("negative", 0.0, 0.0, True),
        ("negative", 1.0, 0.01, False),
    ],
)
def test_each_suite_is_judged_by_its_own_gate(suite, lower, false_adds, clears):
    """P3: `add` and `decoy` clear on their Wilson bound, the two controls on no false add."""
    policy = _proven(_eval(suite, lower=lower, false_adds=false_adds))
    assert (policy.proven == policy.measured) is clears
