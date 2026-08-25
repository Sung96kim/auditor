"""The AST-fact verifier (spec 9.2).

Re-extracts the files a proposal names, refuses when one no longer hashes to what the build cached,
and checks the destination's short name against the src node's own fact tuple for that edge kind and
call form. Pure: the caller does the reading and hands the facts in.
"""

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from auditor.graph.extract import extract_file_facts
from auditor.graph.hashes import file_hashes, node_truth_sha
from auditor.graph.model import (
    FUNCTION_KINDS,
    CallForm,
    EdgeKind,
    GraphNode,
    NodeKind,
    UnresolvedRow,
)
from auditor.graph.refine.models import (
    EDGE_PROPOSAL_KINDS,
    Anchor,
    Proposal,
    ProposedEdge,
    RefinementKind,
)
from auditor.graph.refine.namespace import file_of, short_name
from auditor.graph.resolve_edges import NameBindings, call_forms, form_for
from auditor.roles import RoleClassifier

#: the node kinds the resolver's own endpoints have, src first (spec 9.2 "src/dst kinds obey the
#: resolver's rules"): `overrides` runs method to method, `resolve_edges.py:552`. Public because
#: it is a claim about the resolver, and a test holds it to the edges the resolver really emits.
ENDPOINT_KINDS: dict[EdgeKind, tuple[frozenset[NodeKind], frozenset[NodeKind]]] = {
    EdgeKind.CALLS: (frozenset(FUNCTION_KINDS), frozenset(FUNCTION_KINDS)),
    EdgeKind.REFERENCES_TYPE: (
        frozenset(FUNCTION_KINDS),
        frozenset({NodeKind.CLASS}),
    ),
    EdgeKind.CALLBACK_ARG: (frozenset(FUNCTION_KINDS), frozenset(FUNCTION_KINDS)),
    EdgeKind.INHERITS: (frozenset({NodeKind.CLASS}), frozenset({NodeKind.CLASS})),
    EdgeKind.OVERRIDES: (
        frozenset({NodeKind.METHOD}),
        frozenset({NodeKind.METHOD}),
    ),
}


class VerifyStatus(StrEnum):
    """Why a proposal passed or failed the fact check."""

    OK = "ok"  # the facts support an edge of this shape, not that this dst is the only one
    UNVERIFIED = (
        "unverified"  # a kind spec 9.2 gives no verifier; accepted, tiered on shape
    )
    STALE_FILE = "stale_file"
    NO_SUCH_PATH = "no_such_path"
    NOT_LOADED = "not_loaded"
    NO_SRC_NODE = "no_src_node"
    NO_FACT = "no_fact"
    EXTERNALLY_BOUND = "externally_bound"
    NOT_A_DEFINER = "not_a_definer"
    BAD_NODE_KIND = "bad_node_kind"


#: the two statuses a proposal may still be stored under
_ACCEPTING = frozenset({VerifyStatus.OK, VerifyStatus.UNVERIFIED})


class VerifyResult(BaseModel):
    """One proposal's fact check. ``detail`` names the thing that failed, never a rule id."""

    model_config = ConfigDict(frozen=True)

    status: VerifyStatus
    detail: str = ""

    @property
    def accepted(self) -> bool:
        """Whether the proposal may be stored at all."""
        return self.status in _ACCEPTING

    @property
    def checked(self) -> bool:
        """Whether the AST-fact check actually ran, which is what tier B's gate reads."""
        return self.status is VerifyStatus.OK


class FileFacts(BaseModel):
    """One file as the verifier sees it: what the build cached, and what is on disk right now."""

    model_config = ConfigDict(frozen=True)

    path: str
    cached_truth: str
    fresh_truth: str
    nodes: tuple[GraphNode, ...] = ()

    @classmethod
    def of(
        cls, root: Path, path: str, cached_truth: str, roles: RoleClassifier
    ) -> "FileFacts":
        """Re-read and re-extract one file, classifying it the way the scan did."""
        source = (root / path).read_text(encoding="utf-8", errors="replace")
        facts = extract_file_facts(path, source, roles.classify(path, source).value)
        return cls(
            path=path,
            cached_truth=cached_truth,
            fresh_truth=file_hashes(facts.nodes).truth,
            nodes=tuple(facts.nodes),
        )

    @property
    def current(self) -> bool:
        """Whether the file still produces the facts the build was made from."""
        return self.fresh_truth == self.cached_truth

    def node(self, node_id: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)


class FactVerifier(BaseModel):
    """Spec 9.2's checks over the files a proposal names.

    `VerifyStatus.OK` means the src node's facts support an edge of this shape to a node that
    defines the name. With more than one definer it does not mean this destination is the right
    one; that is what the tier does (>1 definer is tier C, which lands `pending`).
    """

    model_config = ConfigDict(frozen=True)

    files: dict[str, FileFacts] = Field(default_factory=dict)
    bindings: NameBindings = Field(default_factory=NameBindings)
    #: paths the caller found no file for, which is a different answer than a file that moved
    missing: frozenset[str] = frozenset()

    @staticmethod
    def _anchored_ids(proposal: Proposal, row: UnresolvedRow | None) -> tuple[str, ...]:
        """Every node id a proposal is pinned to, its resolution path included, each one once."""
        ids = (*proposal.anchored_ids(), *(row.resolution_path if row else ()))
        return tuple(dict.fromkeys(ids))

    @staticmethod
    def paths_named(proposal: Proposal, row: UnresolvedRow | None) -> tuple[str, ...]:
        """Every file a proposal touches, which is what a caller loads before building one.

        Its anchored nodes (spec 5.5) plus the modules the resolver walked to reach the name.
        """
        return tuple(
            dict.fromkeys(file_of(i) for i in FactVerifier._anchored_ids(proposal, row))
        )

    def check(
        self,
        proposal: Proposal,
        *,
        row: UnresolvedRow | None,
        definers: Sequence[str],
    ) -> VerifyResult:
        """Spec 9.2's table for one proposal, in the order that gives the most useful message.

        A name the caller's module imported from outside the repo is answered as such before the
        endpoint kinds are read: the node kind is a true statement about the wrong problem.
        """
        unusable = self._unusable_path(proposal, row)
        if unusable is not None:
            return unusable
        edge = proposal.edge()
        if proposal.kind not in EDGE_PROPOSAL_KINDS or edge is None:
            return VerifyResult(status=VerifyStatus.UNVERIFIED)
        outside = self._outside_the_answer(edge, proposal.kind, row, definers)
        if outside is not None:
            return outside
        src, dst, kind = edge.src, edge.dst, edge.kind
        if kind is EdgeKind.OVERRIDES and "." not in src.partition("::")[2]:
            return VerifyResult(
                status=VerifyStatus.BAD_NODE_KIND,
                detail=f"{src} is not a method, so it overrides nothing",
            )
        owner = self._owner(src, kind)
        if owner is None:
            return VerifyResult(status=VerifyStatus.NO_SRC_NODE, detail=src)
        forms = call_forms(owner)
        short = short_name(dst)
        site = self._call_site(owner, forms, short, row)
        if site is None and kind is EdgeKind.CALLS:
            return VerifyResult(
                status=VerifyStatus.NO_FACT,
                detail=f"{src} binds {short} itself, so its call is no calls fact",
            )
        # only `calls` picks its fact tuple by call form; the other four have one tuple each
        call_form, receivers = site or (CallForm.BARE, ())
        if self.bindings.externally_bound(owner.module, short, *receivers):
            return VerifyResult(
                status=VerifyStatus.EXTERNALLY_BOUND,
                detail=(
                    f"{owner.module} binds "
                    f"{', '.join(r for r in receivers if r) or short} from outside the repo"
                ),
            )
        endpoint = self._endpoint_kinds(src, dst, kind)
        if endpoint is not None:
            return VerifyResult(status=VerifyStatus.BAD_NODE_KIND, detail=endpoint)
        if short not in self._facts(owner, forms, kind, call_form):
            named = f" as a {call_form.value} call" if kind is EdgeKind.CALLS else ""
            return VerifyResult(
                status=VerifyStatus.NO_FACT,
                detail=f"{src} has no {kind.value} fact naming {short}{named}",
            )
        return VerifyResult(status=VerifyStatus.OK)

    def anchors(
        self, proposal: Proposal, *, row: UnresolvedRow | None
    ) -> tuple[Anchor, ...]:
        """One anchor per node the proposal depends on, hashed from the facts on disk (spec 5.5)."""
        out: list[Anchor] = []
        for node_id in self._anchored_ids(proposal, row):
            facts = self.files.get(file_of(node_id))
            node = facts.node(node_id) if facts else None
            if facts is not None and node is not None:
                out.append(
                    Anchor(
                        node_id=node_id,
                        path=facts.path,
                        truth_sha=node_truth_sha(node),
                        file_sha=facts.fresh_truth,
                    )
                )
        return tuple(out)

    def _unusable_path(
        self, proposal: Proposal, row: UnresolvedRow | None
    ) -> VerifyResult | None:
        """The first file the proposal names that this checkout cannot answer for, as a result.

        A path with no file, a path the caller never loaded and a file whose facts moved are three
        different answers: only the last one is fixed by rebuilding the graph.
        """
        for path in self.paths_named(proposal, row):
            if path in self.missing:
                return VerifyResult(
                    status=VerifyStatus.NO_SUCH_PATH,
                    detail=f"{path} is not a file in this checkout",
                )
            facts = self.files.get(path)
            if facts is None:
                return VerifyResult(
                    status=VerifyStatus.NOT_LOADED,
                    detail=f"{path} was never loaded, so nothing here can answer for it",
                )
            if not facts.current:
                return VerifyResult(
                    status=VerifyStatus.STALE_FILE,
                    detail=(
                        f"{path} changed since the graph was built; "
                        "run `auditr graph build` first"
                    ),
                )
        return None

    def _outside_the_answer(
        self,
        edge: ProposedEdge,
        kind: RefinementKind,
        row: UnresolvedRow | None,
        definers: Sequence[str],
    ) -> VerifyResult | None:
        """Whether the destination is outside the set spec 9.2 lets this kind choose from.

        `resolve_ambiguous` picks from the row's gated candidates, which is narrower than the
        role-filtered definers every other kind is held to.
        """
        if edge.dst not in definers:
            return VerifyResult(
                status=VerifyStatus.NOT_A_DEFINER,
                detail=f"{edge.dst} does not define {edge.name}",
            )
        if (
            kind is RefinementKind.RESOLVE_AMBIGUOUS
            and row is not None
            and edge.dst not in row.candidates
        ):
            return VerifyResult(
                status=VerifyStatus.NOT_A_DEFINER,
                detail=(
                    f"{edge.dst} is not one of the candidates the resolver gated "
                    f"for {edge.name}"
                ),
            )
        return None

    def _owner(self, src: str, kind: EdgeKind) -> GraphNode | None:
        """The node whose facts back this edge kind: the src, except `overrides`, whose fact is
        the owning class's method list (spec 9.2).

        The split is on the qualname half only: a node id carries the file extension, so splitting
        the whole id on `.` turns `a.py::f` into `a`.
        """
        node_id = src
        if kind is EdgeKind.OVERRIDES:
            path, _, qual = src.partition("::")
            node_id = f"{path}::{qual.rsplit('.', 1)[0]}"
        facts = self.files.get(file_of(node_id))
        return facts.node(node_id) if facts else None

    def _call_site(
        self,
        owner: GraphNode,
        forms: dict[tuple[str, CallForm], tuple[str | None, ...]],
        short: str,
        row: UnresolvedRow | None,
    ) -> tuple[CallForm, tuple[str | None, ...]] | None:
        """How the src really calls ``short`` and on which receiver roots, or ``None`` when the
        queue's own rule says there is no placeable fact.

        The row answers when there is one; otherwise `form_for` answers from the same facts under
        the same rule, so a name the src binds itself is dropped here exactly as the queue drops it.
        """
        if row is not None:
            return row.call_form, (row.receiver_root,)
        return form_for(forms, short, owner.local_names)

    def _endpoint_kinds(self, src: str, dst: str, kind: EdgeKind) -> str | None:
        """The reason the endpoints do not obey the resolver's kind rules, or ``None``."""
        for node_id, allowed in zip((src, dst), ENDPOINT_KINDS[kind], strict=True):
            facts = self.files.get(file_of(node_id))
            node = facts.node(node_id) if facts else None
            if node is None:
                return f"{node_id} is not a node in its file"
            if node.kind not in allowed:
                kinds = sorted(k.value for k in allowed)
                return f"{node_id} is a {node.kind.value}, not one of {kinds}"
        return None

    def _facts(
        self,
        owner: GraphNode,
        forms: dict[tuple[str, CallForm], tuple[str | None, ...]],
        kind: EdgeKind,
        call_form: CallForm,
    ) -> frozenset[str]:
        """The fact tuple spec 9.2 pairs with this edge kind and call form."""
        if kind is EdgeKind.CALLS:
            return frozenset(name for name, form in forms if form is call_form)
        if kind is EdgeKind.REFERENCES_TYPE:
            return frozenset(owner.param_types) | frozenset(owner.class_refs)
        if kind is EdgeKind.CALLBACK_ARG:
            return frozenset(owner.callback_names)
        if kind is EdgeKind.INHERITS:
            return frozenset(owner.bases)
        return frozenset(
            owner.method_names
        )  # `overrides`, the fifth and last of the kinds
