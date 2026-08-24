"""Spec 9.1's commit-time conflict rules.

Read against the refinements already active and the resolver's own edges leaving the source, so a
second proposal for a settled edge is answered rather than stacked on top of the first.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from auditor.graph.model import Provenance
from auditor.graph.refine.models import (
    ACTIVE_STATUSES,
    Proposal,
    Refinement,
    RefinementKind,
    RefinementStatus,
)

#: the kinds that put an edge in the graph, and therefore the only ones that can collide
_EDGE_KINDS = frozenset(
    {
        RefinementKind.ADD_EDGE,
        RefinementKind.RETARGET_EDGE,
        RefinementKind.RESOLVE_AMBIGUOUS,
    }
)


def _short(node_id: str) -> str:
    """The bare symbol name inside a node id: ``g`` for ``b.py::Klass.g``."""
    return node_id.split("::")[-1].rsplit(".", 1)[-1]


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
    deterministic: tuple[tuple[str, str, str], ...] = ()

    @classmethod
    def of(
        cls, active: Sequence[Refinement], edges: Sequence[Mapping[str, Any]]
    ) -> "ConflictRules":
        """Keep the active edge-shaped refinements and the resolver's own edges.

        A `refined` edge is another refinement's work, which the first rule already covers; counting
        it here would reject a proposal for being its own duplicate. ``edges`` is whatever the
        caller collected: `GraphDB.edges_of` answers ``src = ? OR dst = ?``, so a caller that does
        not filter hands in inbound rows too. They are dropped by the source comparison below, but
        the caller should not send them.
        """
        return cls(
            active=tuple(
                r
                for r in active
                if r.status in ACTIVE_STATUSES and r.kind in _EDGE_KINDS
            ),
            deterministic=tuple(
                (str(e["src"]), str(e["kind"]), str(e["dst"]))
                for e in edges
                if e.get("provenance", Provenance.DETERMINISTIC.value)
                == Provenance.DETERMINISTIC.value
            ),
        )

    def check(self, proposal: Proposal) -> Conflict | None:
        """The first rule this proposal trips, or ``None``."""
        if proposal.kind not in _EDGE_KINDS:
            return None
        src, dst = proposal.edge_pair()
        kind = proposal.target.edge_kind
        if src is None or dst is None or kind is None:
            return None
        name = proposal.target.name or ""
        prior = self._prior(src, dst, kind.value, name)
        if prior is not None:
            return prior
        return self._deterministic(src, dst, kind.value, name)

    def _prior(self, src: str, dst: str, kind: str, name: str) -> Conflict | None:
        """An active refinement that already answers this edge *for this name*, either the same
        way or another.

        The short name is half the key. Without it, one accepted correction from a source would
        reject every other unplaced call that source makes.
        """
        short = name or _short(dst)
        for refinement in self.active:
            other_src, other_dst = refinement.edge_pair()
            if other_src != src or refinement.target.edge_kind != kind:
                continue
            if other_dst is None or _short(other_dst) != short:
                continue
            if other_dst == dst:
                return Conflict(
                    kind=ConflictKind.DUPLICATE,
                    detail=f"refinement {refinement.refinement_id} already adds this edge",
                    prior_id=refinement.refinement_id,
                )
            return Conflict(
                kind=ConflictKind.CONTRADICTS,
                detail=(
                    f"refinement {refinement.refinement_id} already points {src} at "
                    f"{other_dst} for this name"
                ),
                prior_id=refinement.refinement_id,
            )
        return None

    def _deterministic(
        self, src: str, dst: str, kind: str, name: str
    ) -> Conflict | None:
        """A resolver edge of the same kind leaving ``src`` for a symbol of the same short name."""
        short = name or _short(dst)
        for other_src, other_kind, other_dst in self.deterministic:
            if other_src != src or other_kind != kind:
                continue
            if _short(other_dst) != short:
                continue
            if other_dst == dst:
                return Conflict(
                    kind=ConflictKind.REDUNDANT,
                    detail="the resolver already produces this edge",
                )
            return Conflict(
                kind=ConflictKind.ALREADY_RESOLVED,
                detail=(
                    f"{src} already has a deterministic {kind} edge to {other_dst}; "
                    "correct it with retarget_edge, not add_edge"
                ),
            )
        return None
