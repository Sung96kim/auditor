"""The brief one refinement run is given (spec 9.1, 9.4).

The queue rows under a scope, each with the facts the verifier will check them against, rendered as
the plain text a runner sends verbatim. Reads through `facts.FactReader`, so a brief and a proposal
see one checkout.
"""

import logging
import textwrap
from collections.abc import Iterable, Sequence
from typing import get_args

from pydantic import BaseModel, ConfigDict, computed_field

from auditor.graph.model import (
    QUEUE_ID_CAP,
    FactKind,
    UnresolvedReason,
    UnresolvedRow,
)
from auditor.graph.refine.facts import FactReader
from auditor.graph.refine.models import (
    Refinement,
    RefinementKind,
    RefinementStatus,
    Verdict,
)
from auditor.graph.refine.namespace import file_of, scope_path, short_name, under_scope
from auditor.graph.refine.prompts import RunAnswer
from auditor.graph.refine.verify import FactVerifier, FileFacts
from auditor.graph.resolve_edges import EDGE_KIND_BY_FACT
from auditor.user_settings import LimitsConfig

logger = logging.getLogger(__name__)

#: the widest line the rendered brief may contain, so a terminal and a model both read it whole
LINE_WIDTH = 98
#: the statuses a drifted or staled correction can be found under (spec 5.7)
_STALE_STATUSES = (RefinementStatus.STALE, RefinementStatus.PINNED)
#: the reasons a run may give for stopping, read off the answer so the two cannot drift
_STOPPED_BECAUSE: tuple[str, ...] = get_args(
    RunAnswer.model_fields["stopped_because"].annotation
)
#: the queue reasons whose ``name`` is a cluster's label rather than the anchor node's own name
_CLUSTER_REASONS = frozenset(
    {UnresolvedReason.GENERIC_LABEL, UnresolvedReason.SINGLETON_CLUSTER}
)


def _fold(text: str, *, indent: str = "   ") -> list[str]:
    """One line of the brief as lines no wider than it allows, continuations indented further.

    A token wider than the budget is broken rather than shortened: a node id a proposal cannot
    name back is worse than one that wrapped.
    """
    return textwrap.wrap(
        text,
        width=LINE_WIDTH,
        initial_indent=indent,
        subsequent_indent=f"{indent}   ",
        break_long_words=True,
        break_on_hyphens=False,
    )


def _wrapped(label: str, values: Iterable[str]) -> list[str]:
    """One ``label: a, b, c`` field, or nothing at all when the field is empty."""
    joined = ", ".join(values)
    return _fold(f"{label}: {joined}") if joined else []


def _verdict_line(verdict: Verdict) -> list[str]:
    """One earned verdict as the brief re-reads it back to the run that earned it."""
    return _fold(
        f"{verdict.outcome.value} {verdict.kind.value} tier {verdict.tier.value}: "
        f"{verdict.detail or verdict.status.value}"
    )


class BriefLimits(BaseModel):
    """What this run may not exceed, so the model can plan rather than be cut off."""

    model_config = ConfigDict(frozen=True)

    max_changes: int
    max_targets: int


class BriefTarget(UnresolvedRow):
    """One queue row as the model sees it: the row itself, where it lives, and the facts its
    proposal will be checked against.

    The two id lists are capped the way `QueueRowPayload` caps them, for the same reason: a node
    can have dozens of definers, and every one of them is prompt the run pays for.
    """

    model_config = ConfigDict(frozen=True)

    path: str = ""
    line: int = 0
    facts: tuple[str, ...] = ()

    @classmethod
    def of(cls, row: UnresolvedRow, facts: FileFacts) -> "BriefTarget":
        """One row against the file it names, already re-read from disk."""
        node = facts.node(row.node_id)
        edge_kind = EDGE_KIND_BY_FACT.get(row.fact_kind)
        named = (
            FactVerifier.facts_named(node, edge_kind, row.call_form)
            if node is not None and edge_kind is not None
            else frozenset()
        )
        return cls.model_validate(
            {
                **row.model_dump(),
                "definers": row.definers[:QUEUE_ID_CAP],
                "candidates": row.candidates[:QUEUE_ID_CAP],
                "path": facts.path,
                "line": node.line if node is not None else 0,
                "facts": tuple(sorted(named)),
            }
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed(self) -> tuple[RefinementKind, ...]:
        """The actions this row admits: an edge needs somewhere to point, the other two never do."""
        kinds: list[RefinementKind] = []
        if self.definers:
            kinds.append(RefinementKind.ADD_EDGE)
        if self.candidates:
            kinds.append(RefinementKind.RESOLVE_AMBIGUOUS)
        return (*kinds, RefinementKind.ANNOTATE_NODE, RefinementKind.UNRESOLVABLE)

    def _subject(self) -> str:
        """What this row is about: a name at a call site, a cluster's label, or the node itself."""
        if self.reason in _CLUSTER_REASONS:
            return f"the cluster {self.name!r} anchored here"
        if self.fact_kind is FactKind.NODE:
            return f"the node {self.name!r}"
        called = f"called {self.call_form.value}"
        if self.receiver_root:
            called += f" on {self.receiver_root}"
        return f"the {self.fact_kind.value} {self.name!r}, {called}"

    def render(self, position: int) -> list[str]:
        """This target as the numbered block the brief prints."""
        lines = _fold(f"{position}. {self.node_id}", indent="")
        lines += _fold(
            f"at {self.path}:{self.line}, {self.reason.value}: {self._subject()}"
        )
        lines += _wrapped("candidates", self.candidates)
        lines += _wrapped("definers", self.definers)
        lines += _wrapped("resolution path", self.resolution_path)
        lines += _wrapped(f"facts on {short_name(self.node_id)}", self.facts)
        lines += _wrapped("actions", (k.value for k in self.allowed))
        return lines


class StaleNote(BaseModel):
    """A correction here that the graph no longer trusts, so a run does not re-make it blindly."""

    model_config = ConfigDict(frozen=True)

    refinement_id: int
    kind: RefinementKind
    status: RefinementStatus
    target: str

    @classmethod
    def of(cls, row: Refinement) -> "StaleNote":
        """One stored correction as the warning a brief carries."""
        return cls(
            refinement_id=row.refinement_id,
            kind=row.kind,
            status=row.status,
            target=row.points_at(),
        )

    def render(self) -> list[str]:
        """This note as the one line the brief prints."""
        return _fold(
            f"{self.refinement_id} {self.kind.value} {self.status.value}: {self.target}"
        )


class Brief(BaseModel):
    """Everything one run is told, and the text it is told it in."""

    model_config = ConfigDict(frozen=True)

    scope: str = ""
    commit_sha: str | None = None
    targets: tuple[BriefTarget, ...] = ()
    queue_total: int = 0
    stale: tuple[StaleNote, ...] = ()
    limits: BriefLimits
    #: the verdicts this run has earned, appended when the run re-reads its own brief
    staged: tuple[Verdict, ...] = ()

    def render(self) -> str:
        """The prompt a runner sends, pinned by a golden file: regenerating it is a real edit."""
        lines = ["Refinement brief", ""]
        lines += _fold(f"scope: {self.scope or '(the whole repo)'}", indent="")
        lines += _fold(
            f"commit: {self.commit_sha or '(not a git checkout)'}", indent=""
        )
        lines += _fold(
            f"targets: {len(self.targets)} of {self.queue_total} queue rows under this scope",
            indent="",
        )
        lines += _fold(
            f"limits: {self.limits.max_targets} targets per run, "
            f"{self.limits.max_changes} corrections per run",
            indent="",
        )
        lines += ["", "Targets", ""]
        if not self.targets:
            lines += [*_fold("none: nothing under this scope is unresolved."), ""]
        for position, target in enumerate(self.targets, start=1):
            lines += target.render(position)
            lines.append("")
        lines += ["Stale corrections", ""]
        lines += [line for note in self.stale for line in note.render()] or _fold(
            "none."
        )
        if self.staged:
            lines += ["", "Verdicts so far", ""]
            lines += [line for v in self.staged for line in _verdict_line(v)]
        lines += ["", "What to do", ""]
        lines += _wrapped("actions available here", sorted(self._actions()))
        lines += _fold(
            "Read the source before every proposal, then call mcp__graph__propose once per "
            "target at most. Call mcp__graph__brief to re-read this with your verdicts."
        )
        lines.append("")
        lines += _fold(
            "Answer with: summary (one line), proposed (how many you made), stopped_because "
            f"({' | '.join(_STOPPED_BECAUSE)})."
        )
        return "\n".join(lines) + "\n"

    def _actions(self) -> set[str]:
        """Every action some target here admits, so the closing section names no dead one."""
        return {kind.value for target in self.targets for kind in target.allowed}


class BriefBuilder(BaseModel):
    """Builds one brief from the queue, under this user's per-run limits."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    facts: FactReader
    limits: LimitsConfig

    async def build(self, scope: str, *, commit_sha: str | None = None) -> Brief:
        """The brief for one scope. Only the rows the run may work on are decoded."""
        scope = scope_path(scope)
        prefix = scope or None
        graph = self.facts.index.graph
        queue_total = await graph.count_unresolved(prefix, external=False)
        rows = [
            UnresolvedRow.model_validate(row)
            for row in await graph.unresolved(
                prefix=prefix, external=False, limit=self.limits.max_nodes_per_run
            )
        ]
        loaded, _missing = await self.facts.files(
            tuple(dict.fromkeys(file_of(row.node_id) for row in rows))
        )
        return Brief(
            scope=scope,
            commit_sha=commit_sha,
            targets=self._targets(rows, loaded),
            queue_total=queue_total,
            stale=await self._stale(scope),
            limits=BriefLimits(
                max_changes=self.limits.max_changes_per_run,
                max_targets=self.limits.max_nodes_per_run,
            ),
        )

    @staticmethod
    def _targets(
        rows: Sequence[UnresolvedRow], loaded: dict[str, FileFacts]
    ) -> tuple[BriefTarget, ...]:
        """One target per row this checkout can answer for; the verifier would refuse the rest."""
        out: list[BriefTarget] = []
        for row in rows:
            facts = loaded.get(file_of(row.node_id))
            if facts is None:
                logger.warning(
                    "skipping queue row %s: this checkout cannot read %s",
                    row.node_id,
                    file_of(row.node_id),
                )
                continue
            out.append(BriefTarget.of(row, facts))
        return tuple(out)

    async def _stale(self, scope: str) -> tuple[StaleNote, ...]:
        """Corrections under this scope the graph stopped trusting: staled, or a pinned one that
        drifted (spec 5.7)."""
        rows = await self.facts.index.refinements.refinements(statuses=_STALE_STATUSES)
        return tuple(
            StaleNote.of(row)
            for row in rows
            if (row.status is RefinementStatus.STALE or row.drifted)
            # every id, the rule `StagedRun.covers` applies: a correction this run could not have
            # made is not one it needs warning off
            and all(under_scope(node_id, scope) for node_id in row.anchored_ids())
        )
