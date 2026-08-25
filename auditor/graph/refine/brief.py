"""The brief one refinement run is given (spec 9.1, 9.4).

The queue rows under a scope, each with the facts the verifier will check them against, rendered as
the plain text a runner sends verbatim. Reads through `facts.FactReader`, so a brief and a proposal
see one checkout.
"""

import logging
import textwrap
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from auditor.graph.model import CallForm, FactKind, UnresolvedReason, UnresolvedRow
from auditor.graph.refine.facts import FactReader
from auditor.graph.refine.models import (
    RefinementKind,
    RefinementStatus,
    Verdict,
)
from auditor.graph.refine.namespace import file_of, scope_path, short_name, under_scope
from auditor.graph.refine.prompts import RUN_ANSWER_SCHEMA
from auditor.graph.refine.verify import FactVerifier, FileFacts
from auditor.graph.resolve_edges import EDGE_KIND_BY_FACT
from auditor.user_settings import LimitsConfig

logger = logging.getLogger(__name__)

#: the widest line the rendered brief may contain, so a terminal and a model both read it whole
LINE_WIDTH = 98
#: the statuses a drifted or staled correction can be found under (spec 5.7)
_STALE_STATUSES = (RefinementStatus.STALE, RefinementStatus.PINNED)
#: the reasons a run may give for stopping, read off the schema so the two cannot drift
_STOPPED_BECAUSE: tuple[str, ...] = tuple(
    RUN_ANSWER_SCHEMA["properties"]["stopped_because"]["enum"]
)


def _fold(text: str) -> list[str]:
    """One sentence as indented lines no wider than the brief allows."""
    return textwrap.wrap(
        text,
        width=LINE_WIDTH,
        initial_indent="   ",
        subsequent_indent="      ",
        break_long_words=False,
        break_on_hyphens=False,
    )


def _wrapped(label: str, values: Iterable[str]) -> list[str]:
    """One ``label: a, b, c`` field, or nothing at all when the field is empty."""
    joined = ", ".join(values)
    return _fold(f"{label}: {joined}") if joined else []


class BriefLimits(BaseModel):
    """What this run may not exceed, so the model can plan rather than be cut off."""

    model_config = ConfigDict(frozen=True)

    max_changes: int
    max_targets: int


class BriefTarget(BaseModel):
    """One queue row as the model sees it: the row, and the facts its proposal will be checked
    against."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    name: str
    path: str
    line: int = 0
    reason: UnresolvedReason
    fact_kind: FactKind
    call_form: CallForm = CallForm.BARE
    receiver_root: str | None = None
    candidates: tuple[str, ...] = ()
    definers: tuple[str, ...] = ()
    resolution_path: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    allowed: tuple[RefinementKind, ...] = ()

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
        return cls(
            node_id=row.node_id,
            name=row.name,
            path=facts.path,
            line=node.line if node is not None else 0,
            reason=row.reason,
            fact_kind=row.fact_kind,
            call_form=row.call_form,
            receiver_root=row.receiver_root,
            candidates=row.candidates,
            definers=row.definers,
            resolution_path=row.resolution_path,
            facts=tuple(sorted(named)),
            allowed=cls._allowed(row),
        )

    @staticmethod
    def _allowed(row: UnresolvedRow) -> tuple[RefinementKind, ...]:
        """The actions this row admits: an edge needs somewhere to point, the other two never do."""
        kinds: list[RefinementKind] = []
        if row.definers:
            kinds.append(RefinementKind.ADD_EDGE)
        if row.candidates:
            kinds.append(RefinementKind.RESOLVE_AMBIGUOUS)
        return (*kinds, RefinementKind.ANNOTATE_NODE, RefinementKind.UNRESOLVABLE)

    def _subject(self) -> str:
        """What this row is about: a name at a call site, or the node itself for a build row."""
        if self.fact_kind is FactKind.NODE:
            return f"the node {self.name!r}"
        called = f"called {self.call_form.value}"
        if self.receiver_root:
            called += f" on {self.receiver_root}"
        return f"the {self.fact_kind.value} {self.name!r}, {called}"

    def render(self, position: int) -> list[str]:
        """This target as the numbered block the brief prints."""
        lines = [f"{position}. {self.node_id}"]
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
        lines = [
            "Refinement brief",
            "",
            f"scope: {self.scope or '(the whole repo)'}",
            f"commit: {self.commit_sha or '(not a git checkout)'}",
            f"targets: {len(self.targets)} of {self.queue_total} queue rows under this scope",
            f"limits: {self.limits.max_targets} targets per run, "
            f"{self.limits.max_changes} corrections per run",
            "",
            "Targets",
            "",
        ]
        if not self.targets:
            lines.append("   none: nothing under this scope is unresolved.")
        for position, target in enumerate(self.targets, start=1):
            lines += target.render(position)
            lines.append("")
        lines += ["Stale corrections", ""]
        lines += [
            f"   {note.refinement_id} {note.kind.value} {note.status.value}: {note.target}"
            for note in self.stale
        ] or ["   none."]
        if self.staged:
            lines += ["", "Verdicts so far", ""]
            lines += [
                f"   {v.outcome.value} {v.kind.value} tier {v.tier.value}: "
                f"{v.detail or v.status.value}"
                for v in self.staged
            ]
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
            StaleNote(
                refinement_id=row.refinement_id,
                kind=row.kind,
                status=row.status,
                target=_target_line(
                    row.edge_pair(), row.target.node_id, row.target.name
                ),
            )
            for row in rows
            if (row.status is RefinementStatus.STALE or row.drifted)
            # every id, the rule `StagedRun.covers` applies: a correction this run could not have
            # made is not one it needs warning off
            and all(under_scope(node_id, scope) for node_id in row.anchored_ids())
        )


def _target_line(
    edge: tuple[str | None, str | None], node_id: str | None, name: str | None
) -> str:
    """What one stored correction points at, in one line."""
    src, dst = edge
    if src and dst:
        return f"{src} -> {dst}"
    return node_id or name or "(no target)"
