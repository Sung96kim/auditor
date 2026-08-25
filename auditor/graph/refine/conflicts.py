"""Spec 9.1's commit-time conflict rules.

Read against the refinements already active and the resolver's own edges leaving the source, so a
second proposal for a settled edge is answered rather than stacked on top of the first.
"""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from auditor.graph.model import GraphEdge, Provenance
from auditor.graph.refine.models import (
    ACTIVE_STATUSES,
    EDGE_PROPOSAL_KINDS,
    Proposal,
    ProposedEdge,
    Refinement,
    RefinementKind,
    RefinementStatus,
)
from auditor.graph.refine.namespace import short_name


class ConflictKind(StrEnum):
    """Which rule fired: spec 9.1's three, plus spec 5.4's redundancy."""

    DUPLICATE = "duplicate"  # an identical ACTIVE REFINEMENT: the proposal confirms it
    REDUNDANT = (
        "redundant"  # the resolver already produces the edge: terminal (spec 5.4)
    )
    CONTRADICTS = "contradicts"
    ALREADY_RESOLVED = "already_resolved"


class Conflict(BaseModel):
    """One collision, naming the prior work so the caller can look it up."""

    model_config = ConfigDict(frozen=True)

    kind: ConflictKind
    detail: str
    prior_id: int = 0

    @property
    def rewrite_as_confirm(self) -> bool:
        """Whether the proposal is stored as a `confirm_edge` instead of rejected.

        Only an identical active refinement earns that: a deterministic edge is not something a
        caller can confirm, it is already in the graph (spec 5.4, spec 9.1).
        """
        return self.kind is ConflictKind.DUPLICATE

    @property
    def stored_status(self) -> RefinementStatus:
        """The status the refused proposal is stored under: `redundant` is terminal and never
        re-briefed (spec 5.4, spec 5.7), everything else is a plain rejection."""
        return (
            RefinementStatus.REDUNDANT
            if self.kind is ConflictKind.REDUNDANT
            else RefinementStatus.REJECTED
        )


class ConflictRules(BaseModel):
    """The prior work one commit is checked against."""

    model_config = ConfigDict(frozen=True)

    active: tuple[Refinement, ...] = ()
    deterministic: tuple[GraphEdge, ...] = ()

    @classmethod
    def of(
        cls, active: Sequence[Refinement], edges: Sequence[GraphEdge]
    ) -> "ConflictRules":
        """Keep the active edge-shaped refinements and the resolver's own deterministic edges.

        A `refined` edge is another refinement's work, which the first rule already covers; counting
        it here would reject a proposal for being its own duplicate.
        """
        return cls(
            active=tuple(
                r
                for r in active
                if r.status in ACTIVE_STATUSES and r.kind in EDGE_PROPOSAL_KINDS
            ),
            deterministic=tuple(
                e for e in edges if e.provenance is Provenance.DETERMINISTIC
            ),
        )

    def check(self, proposal: Proposal) -> Conflict | None:
        """The first rule this proposal trips, or ``None``.

        The resolver's own edges answer first, so an edge it now produces is terminal rather than
        a confirmation of the refinement that used to place it (spec 5.4).
        """
        edge = proposal.edge()
        if proposal.kind not in EDGE_PROPOSAL_KINDS or edge is None:
            return None
        settled = (
            self._deterministic(edge)
            if proposal.kind is RefinementKind.ADD_EDGE
            else None
        )
        return settled if settled is not None else self._prior(edge)

    def _prior(self, edge: ProposedEdge) -> Conflict | None:
        """An active refinement that already answers this edge *for this name*, either the same
        way or another.

        The short name is half the key. Without it, one accepted correction from a source would
        reject every other unplaced call that source makes.
        """
        for refinement in self.active:
            other = refinement.edge()
            if other is None or other.src != edge.src or other.kind is not edge.kind:
                continue
            if short_name(other.dst) != edge.name:
                continue
            if other.dst == edge.dst:
                return Conflict(
                    kind=ConflictKind.DUPLICATE,
                    detail=f"refinement {refinement.refinement_id} already adds this edge",
                    prior_id=refinement.refinement_id,
                )
            return Conflict(
                kind=ConflictKind.CONTRADICTS,
                detail=(
                    f"refinement {refinement.refinement_id} already points {edge.src} at "
                    f"{other.dst} for this name"
                ),
                prior_id=refinement.refinement_id,
            )
        return None

    def _deterministic(self, edge: ProposedEdge) -> Conflict | None:
        """Spec 9.1's already-resolved rule, which scopes to `add_edge` alone.

        The exact destination is looked for across every resolver edge of the same name before any
        of them is reported as pointing elsewhere; a `retarget_edge` names one of these on purpose.
        """
        settled = [
            other
            for other in self.deterministic
            if other.src == edge.src
            and other.kind is edge.kind
            and short_name(other.dst) == edge.name
        ]
        if any(other.dst == edge.dst for other in settled):
            return Conflict(
                kind=ConflictKind.REDUNDANT,
                detail="the resolver already produces this edge",
            )
        if not settled:
            return None
        return Conflict(
            kind=ConflictKind.ALREADY_RESOLVED,
            detail=(
                f"{edge.src} already has a deterministic {edge.kind.value} edge to "
                f"{settled[0].dst}; correct it with retarget_edge, not add_edge"
            ),
        )
