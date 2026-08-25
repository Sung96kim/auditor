"""The reads a proposal or a brief is judged against (spec 9.2).

`verify.FactVerifier` is pure by contract, so the reading lives here rather than in the service.
Its own module because the brief reads the same files under the same role rules, and a builder
that reached into `service.py` for them would close an import cycle.
"""

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from auditor.database import IndexStore
from auditor.graph.model import NodeKind, UnresolvedRow
from auditor.graph.refine.models import Proposal
from auditor.graph.refine.verify import FactVerifier, FileFacts
from auditor.graph.resolve_edges import NameBindings
from auditor.roles import RoleClassifier


class FactReader(BaseModel):
    """The three reads one proposal is judged against, over the collaborators they need.

    `verify.FactVerifier` is pure by contract, so the reading belongs to an object of its own
    rather than to the service, where it was four private methods and a four-collaborator call.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    index: IndexStore
    root: Path
    roles: RoleClassifier

    async def queue_row(self, proposal: Proposal) -> UnresolvedRow | None:
        """The `graph_unresolved` row this proposal answers, if there is one."""
        node_id = proposal.target.node_id or proposal.target.src
        name = proposal.target.name
        if not node_id or not name:
            return None
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
