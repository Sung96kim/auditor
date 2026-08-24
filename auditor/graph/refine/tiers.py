"""Activation tiers (spec 9.2's table and spec 10.3's gate).

A tier is a property of the proposal's shape; whether that tier activates is a property of what the
eval suites have measured on this repo for this runner and model. With no eval row, spec 10.3 says
`resolve_ambiguous` and every tier B proposal behave as tier C, which is where every repo starts.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from auditor.graph.model import CallForm, UnresolvedRow
from auditor.graph.refine.models import (
    EvalRow,
    Proposal,
    RefinementKind,
    RefinementStatus,
    RunnerKind,
    Tier,
)

#: tier A kinds that need no measurement: none of them can add an edge (spec 10.3)
ALWAYS_ACTIVE = frozenset(
    {
        RefinementKind.CONFIRM_EDGE,
        RefinementKind.RELABEL_CLUSTER,
        RefinementKind.ANNOTATE_NODE,
        RefinementKind.UNRESOLVABLE,
    }
)

#: the two call forms where "the repo defines it, the call is there, nothing else could bind the
#: name" holds (spec 10.1)
_BOUNDED_FORMS = frozenset({CallForm.BARE, CallForm.SELF})

#: suites judged by their precision bound, versus suites judged by having produced no false add
_PRECISION_SUITES = frozenset({"add", "decoy", "fixtures"})


class TierPolicy(BaseModel):
    """One repo's activation policy for one runner and model."""

    model_config = ConfigDict(frozen=True)

    min_precision: float = 0.95
    proven: frozenset[str] = frozenset()

    @classmethod
    def of(
        cls,
        evals: Sequence[EvalRow],
        *,
        min_precision: float,
        runner: RunnerKind,
        model: str,
    ) -> "TierPolicy":
        """Which suites have cleared their own gate here. S7 narrows this to the stratum matching
        a proposal's shape; a suite-level answer is strictly the more conservative one."""
        return cls(
            min_precision=min_precision,
            proven=frozenset(
                row.suite
                for row in evals
                if row.runner is runner
                and row.model == model
                and (
                    row.metrics.lower_bound_95 >= min_precision
                    if row.suite in _PRECISION_SUITES
                    else row.metrics.false_add_rate == 0.0
                )
            ),
        )

    def tier(
        self, proposal: Proposal, *, row: UnresolvedRow | None, verified: bool
    ) -> Tier:
        """Spec 9.2's tier column: the kind decides it, except `add_edge`, whose call form,
        definer count and verifier result do."""
        if (
            proposal.kind in ALWAYS_ACTIVE
            or proposal.kind is RefinementKind.RESOLVE_AMBIGUOUS
        ):
            return Tier.A
        if proposal.kind is not RefinementKind.ADD_EDGE or row is None:
            return Tier.C
        bounded = (
            verified
            and row.call_form in _BOUNDED_FORMS
            and len(row.definers) == 1
            and not row.externally_bound
        )
        return Tier.B if bounded else Tier.C

    def status(self, kind: RefinementKind, tier: Tier) -> RefinementStatus:
        """The status a proposal of this kind and tier is stored under (spec 10.3)."""
        if tier is Tier.A and kind in ALWAYS_ACTIVE:
            return RefinementStatus.ACTIVE
        if tier is Tier.A and "decoy" in self.proven:
            return RefinementStatus.ACTIVE
        if tier is Tier.B and {"add", "collision"} <= self.proven:
            return RefinementStatus.ACTIVE
        return RefinementStatus.PENDING
