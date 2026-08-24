"""Spec 9.2's tier column and spec 10.3's activation table."""

import pytest

from auditor.graph.model import CallForm, EdgeKind, UnresolvedRow
from auditor.graph.refine.models import (
    EvalMetrics,
    EvalRow,
    Proposal,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    RunnerKind,
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


def _eval(suite: str, *, lower: float = 1.0, false_adds: float = 0.0) -> EvalRow:
    return EvalRow(
        repo_identity="/repo/.git",
        runner=RunnerKind.CLAUDE,
        model="haiku",
        suite=suite,
        stratum="same-module",
        metrics=EvalMetrics(lower_bound_95=lower, false_add_rate=false_adds),
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
    unproven = TierPolicy()
    assert (
        unproven.status(RefinementKind.RESOLVE_AMBIGUOUS, Tier.A)
        is RefinementStatus.PENDING
    )
    proven = TierPolicy.of(
        [_eval("decoy")], min_precision=0.95, runner=RunnerKind.CLAUDE, model="haiku"
    )
    assert (
        proven.status(RefinementKind.RESOLVE_AMBIGUOUS, Tier.A)
        is RefinementStatus.ACTIVE
    )


def test_tier_b_needs_both_the_add_suite_and_the_collision_control():
    add_only = TierPolicy.of(
        [_eval("add")], min_precision=0.95, runner=RunnerKind.CLAUDE, model="haiku"
    )
    assert add_only.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.PENDING
    both = TierPolicy.of(
        [_eval("add"), _eval("collision", lower=0.0)],
        min_precision=0.95,
        runner=RunnerKind.CLAUDE,
        model="haiku",
    )
    assert both.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.ACTIVE


def test_a_collision_control_with_a_false_add_proves_nothing():
    policy = TierPolicy.of(
        [_eval("add"), _eval("collision", false_adds=0.01)],
        min_precision=0.95,
        runner=RunnerKind.CLAUDE,
        model="haiku",
    )
    assert policy.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.PENDING


def test_another_runners_eval_row_proves_nothing_here():
    rows = [_eval("add"), _eval("collision", lower=0.0)]
    policy = TierPolicy.of(
        rows, min_precision=0.95, runner=RunnerKind.CODEX, model="haiku"
    )
    assert policy.status(RefinementKind.ADD_EDGE, Tier.B) is RefinementStatus.PENDING


def test_tier_c_is_always_pending():
    assert (
        TierPolicy().status(RefinementKind.ADD_EDGE, Tier.C) is RefinementStatus.PENDING
    )
