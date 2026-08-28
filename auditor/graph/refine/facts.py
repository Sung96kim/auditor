"""The reads a proposal or a brief is judged against, and the brief built from them (spec 9.2).

`verify.FactVerifier` is pure by contract, so the reading lives here rather than in the service.
Its own module because the brief reads the same files under the same role rules, and a builder
that reached into `service.py` for them would close an import cycle.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from auditor.database import IndexStore
from auditor.graph.model import NodeKind, UnresolvedRow
from auditor.graph.refine.brief import (
    Brief,
    BriefLimits,
    BriefTarget,
    StaleNote,
)
from auditor.graph.refine.models import Proposal, RefinementStatus
from auditor.graph.refine.namespace import file_of, scope_path, under_scope
from auditor.graph.refine.verify import FactVerifier, FileFacts
from auditor.graph.resolve_edges import EDGE_KIND_BY_FACT, NameBindings
from auditor.roles import RoleClassifier
from auditor.user_settings import LimitsConfig

logger = logging.getLogger(__name__)

#: the statuses a drifted or staled correction can be found under (spec 5.7)
_STALE_STATUSES = (RefinementStatus.STALE, RefinementStatus.PINNED)


class FactReader(BaseModel):
    """The three reads one proposal is judged against, over the collaborators they need.

    `verify.FactVerifier` is pure by contract, so the reading belongs to an object of its own
    rather than to the service, where it was four private methods and a four-collaborator call.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    index: IndexStore
    root: Path
    roles: RoleClassifier
    #: rows an eval masked into the queue; while they are here the stored queue is not read at all
    synthetic: tuple[UnresolvedRow, ...] = ()

    async def queue(
        self, prefix: str | None, *, limit: int | None, external: bool
    ) -> list[UnresolvedRow]:
        """The queue rows one scope offers, synthetic rows first and alone.

        A reader holding synthetic rows answers from them and nothing else, so an eval brief can
        never be filled out with this checkout's own unresolved rows (spec 10.2). ``external`` does
        not narrow them: a caller that chose these rows means them, and the collision suite is made
        of exactly the externally bound rows a brief hides by default.
        """
        if self.synthetic:
            rows = [
                row for row in self.synthetic if under_scope(row.node_id, prefix or "")
            ]
            return rows if limit is None else rows[:limit]
        return [
            UnresolvedRow.model_validate(row)
            for row in await self.index.graph.unresolved(
                prefix=prefix, external=external, limit=limit
            )
        ]

    async def count_queue(self, prefix: str | None, *, external: bool) -> int:
        """How many rows that scope holds, under the same synthetic-rows-only rule as `queue`."""
        if self.synthetic:
            return len(await self.queue(prefix, limit=None, external=external))

        return await self.index.graph.count_unresolved(prefix, external=external)

    async def queue_row(self, proposal: Proposal) -> UnresolvedRow | None:
        """The `graph_unresolved` row this proposal answers, if there is one."""
        node_id = proposal.target.node_id or proposal.target.src
        name = proposal.target.name
        if not node_id or not name:
            return None
        if self.synthetic:
            return next(
                (
                    row
                    for row in self.synthetic
                    if row.node_id == node_id and row.name == name
                ),
                None,
            )
        rows = await self.index.graph.unresolved(node_ids=[node_id])
        return next(
            (UnresolvedRow.model_validate(r) for r in rows if r["name"] == name), None
        )

    async def definers(
        self, proposal: Proposal, row: UnresolvedRow | None
    ) -> Sequence[str]:
        """The role-filtered definitions of the called name: the queue row's when there is one,
        otherwise the graph's own answer (spec 9.2's `retarget_edge` row needs it)."""
        if row is not None:
            return row.definers
        name = proposal.target.name
        return await self.index.graph.definers(name) if name else []

    async def files(
        self, paths: Sequence[str]
    ) -> tuple[dict[str, FileFacts], frozenset[str]]:
        """Re-read the named files, and the paths this checkout holds no file for.

        A path with no file and a path the build never cached are different answers, so the second
        is simply left out and the verifier says `not_loaded` rather than guessing.
        """
        loaded: dict[str, FileFacts] = {}
        missing: set[str] = set()
        for path in paths:
            if not (self.root / path).is_file():
                missing.add(path)  # a path that never existed, not a file that moved
                continue
            cached = await self.index.graph.hashes(path)
            if cached is None:
                continue
            loaded[path] = FileFacts.of(self.root, path, cached.truth, self.roles)
        return loaded, frozenset(missing)

    async def verifier(
        self, proposal: Proposal, row: UnresolvedRow | None
    ) -> FactVerifier:
        """A verifier holding the files this proposal names, re-read from disk."""
        files, missing = await self.files(FactVerifier.paths_named(proposal, row))
        modules = [
            node
            for facts in files.values()
            for node in facts.nodes
            if node.kind is NodeKind.MODULE
        ]
        return FactVerifier(
            files=files,
            bindings=NameBindings.of(
                modules, module_ids=await self.index.graph.module_ids()
            ),
            missing=missing,
        )


class BriefBuilder(BaseModel):
    """Builds one brief from the queue, under this user's per-run limits.

    Here rather than beside the models it builds: it reads the queue, the stored corrections and
    the files on disk, and `brief.py` is imported by every fast CLI command through the wire
    payload.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    facts: FactReader
    limits: LimitsConfig

    async def build(self, scope: str, *, commit_sha: str | None = None) -> Brief:
        """The brief for one scope. Only the rows the run may work on are decoded."""
        scope = scope_path(scope)
        prefix = scope or None
        queue_total = await self.facts.count_queue(prefix, external=False)
        rows = await self.facts.queue(
            prefix, limit=self.limits.max_nodes_per_run, external=False
        )
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
            out.append(_target(row, facts))
        return tuple(out)

    async def _stale(self, scope: str) -> tuple[StaleNote, ...]:
        """Corrections under this scope the graph stopped trusting: staled, or a pinned one that
        drifted (spec 5.7)."""
        rows = await self.facts.index.refinements.refinements(statuses=_STALE_STATUSES)
        return tuple(
            StaleNote.of(row)
            for row in rows
            if (row.status is RefinementStatus.STALE or row.drifted)
            # `StagedRun.covers` again: a correction this run could not have made needs no warning
            and all(under_scope(node_id, scope) for node_id in row.anchored_ids())
        )


def _target(row: UnresolvedRow, facts: FileFacts) -> BriefTarget:
    """One queue row against the file it names, already re-read from disk.

    A function rather than a method: it composes a stored row with a freshly extracted file, and
    neither of those two models owns the other.
    """
    node = facts.node(row.node_id)
    edge_kind = EDGE_KIND_BY_FACT.get(row.fact_kind)
    named = (
        FactVerifier.facts_named(node, edge_kind, row.call_form)
        if node is not None and edge_kind is not None
        else frozenset()
    )
    return BriefTarget.of(
        row,
        path=facts.path,
        line=node.line if node is not None else 0,
        facts=sorted(named),
    )
