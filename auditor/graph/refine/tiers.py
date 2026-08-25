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
    Stratum,
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
    """One repo's activation policy for one runner and model, per eval stratum."""

    model_config = ConfigDict(frozen=True)

    #: every ``(suite, stratum)`` this runner and model has an eval row for
    measured: frozenset[tuple[str, str]] = frozenset()
    #: the subset of them that met the suite's own gate
    proven: frozenset[tuple[str, str]] = frozenset()

    @classmethod
    def of(
        cls,
        evals: Sequence[EvalRow],
        *,
        min_precision: float,
        runner: RunnerKind,
        model: str,
    ) -> "TierPolicy":
        """What this runner and model measured here, and which strata cleared their own gate."""
        rows = [row for row in evals if row.runner is runner and row.model == model]
        return cls(
            measured=frozenset((row.suite, row.stratum) for row in rows),
            proven=frozenset(
                (row.suite, row.stratum)
                for row in rows
                if cls._clears(row, min_precision)
            ),
        )

    @staticmethod
    def _clears(row: EvalRow, min_precision: float) -> bool:
        """Whether one stratum met its gate: a precision suite on its Wilson lower bound, a
        control on having produced no false add, and neither on a run of no trials (spec 10.2)."""
        if row.metrics.n <= 0:
            return False
        if row.suite in _PRECISION_SUITES:
            return row.metrics.lower_bound_95 >= min_precision
        return row.metrics.false_add_rate == 0.0

    def tier(
        self, proposal: Proposal, *, row: UnresolvedRow | None, verified: bool
    ) -> Tier:
        """Spec 9.2's tier column: the kind decides it, except the kinds a verifier answers for,
        whose call form, definer count and verifier result do."""
        if proposal.kind in ALWAYS_ACTIVE:
            return Tier.A
        if proposal.kind is RefinementKind.RESOLVE_AMBIGUOUS:
            return Tier.A if verified else Tier.C
        if proposal.kind is not RefinementKind.ADD_EDGE or row is None:
            return Tier.C
        bounded = (
            verified
            and row.call_form in _BOUNDED_FORMS
            and len(row.definers) == 1
            and not row.externally_bound
        )
        return Tier.B if bounded else Tier.C

    def status(
        self, kind: RefinementKind, tier: Tier, *, stratum: Stratum | None = None
    ) -> RefinementStatus:
        """The status a proposal of this kind and tier is stored under (spec 10.3).

        ``stratum`` is the add suite's stratum for this proposal's own shape; without one every
        stratum the suite measured has to clear, which is the conservative reading.
        """
        if tier is Tier.A and kind in ALWAYS_ACTIVE:
            return RefinementStatus.ACTIVE
        if tier is Tier.A and self._cleared("decoy"):
            return RefinementStatus.ACTIVE
        if (
            tier is Tier.B
            and self._cleared("add", stratum)
            and self._cleared("collision")
        ):
            return RefinementStatus.ACTIVE
        return RefinementStatus.PENDING

    def _cleared(self, suite: str, stratum: Stratum | None = None) -> bool:
        """Whether a suite's gate is met here: the stratum matching the proposal, or every
        stratum the suite measured when there is none to match."""
        rows = {pair for pair in self.measured if pair[0] == suite}
        if not rows:
            return False
        if stratum is None:
            return rows <= self.proven
        return (suite, str(stratum)) in self.proven
