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
from auditor.graph.refine.models import Anchor, Proposal, RefinementKind
from auditor.graph.resolve_edges import NameBindings, call_forms, form_for
from auditor.roles import RoleClassifier

#: the kinds whose destination is backed by a fact, and therefore the only ones with a verifier
VERIFIED_KINDS = frozenset(
    {
        RefinementKind.ADD_EDGE,
        RefinementKind.RETARGET_EDGE,
        RefinementKind.RESOLVE_AMBIGUOUS,
    }
)

#: node kinds each edge kind's endpoints must have (spec 9.2 "src/dst kinds obey the resolver's
#: rules"): the resolver only ever emits these pairings.
_ENDPOINT_KINDS: dict[EdgeKind, tuple[frozenset[NodeKind], frozenset[NodeKind]]] = {
    EdgeKind.CALLS: (frozenset(FUNCTION_KINDS), frozenset(FUNCTION_KINDS)),
    EdgeKind.REFERENCES_TYPE: (
        frozenset(FUNCTION_KINDS),
        frozenset({NodeKind.CLASS}),
    ),
    EdgeKind.CALLBACK_ARG: (frozenset(FUNCTION_KINDS), frozenset(FUNCTION_KINDS)),
    EdgeKind.INHERITS: (frozenset({NodeKind.CLASS}), frozenset({NodeKind.CLASS})),
    # the src half is the *owner* `_owner` resolved, which for `overrides` is the class
    EdgeKind.OVERRIDES: (
        frozenset({NodeKind.CLASS}),
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
    def paths_named(proposal: Proposal, row: UnresolvedRow | None) -> tuple[str, ...]:
        """Every file a proposal touches: its endpoints, its target node, and the modules the
        resolver walked to reach the name (spec 5.5)."""
        src, dst = proposal.edge_pair()
        ids = [
            *(i for i in (src, dst, proposal.target.node_id) if i),
            *(row.resolution_path if row else ()),
        ]
        return tuple(dict.fromkeys(i.split("::")[0] for i in ids))

    def check(
        self,
        proposal: Proposal,
        *,
        row: UnresolvedRow | None,
        definers: Sequence[str],
    ) -> VerifyResult:
        """Spec 9.2's table for one proposal, in the order that gives the most useful message."""
        unusable = self._unusable_path(proposal, row)
        if unusable is not None:
            return unusable
        if proposal.kind not in VERIFIED_KINDS:
            return VerifyResult(status=VerifyStatus.UNVERIFIED)
        src, dst = proposal.edge_pair()
        kind = proposal.target.edge_kind
        if src is None or dst is None or kind is None:  # refused at construction
            return VerifyResult(
                status=VerifyStatus.NO_SRC_NODE, detail="incomplete target"
            )
        if dst not in definers:
            return VerifyResult(
                status=VerifyStatus.NOT_A_DEFINER,
                detail=f"{dst} does not define {proposal.target.name}",
            )
        if kind is EdgeKind.OVERRIDES and "." not in src.partition("::")[2]:
            return VerifyResult(
                status=VerifyStatus.BAD_NODE_KIND,
                detail=f"{src} is not a method, so it overrides nothing",
            )
        owner = self._owner(src, kind)
        if owner is None:
            return VerifyResult(status=VerifyStatus.NO_SRC_NODE, detail=src)
        endpoint = self._endpoint_kinds(owner, dst, kind)
        if endpoint is not None:
            return VerifyResult(status=VerifyStatus.BAD_NODE_KIND, detail=endpoint)
        forms = call_forms(owner)
        short = dst.split("::")[-1].rsplit(".", 1)[-1]
        call_form, receivers = self._call_site(owner, forms, short, row)
        if short not in self._facts(owner, forms, kind, call_form):
            return VerifyResult(
                status=VerifyStatus.NO_FACT,
                detail=f"{src} has no {kind.value} fact naming {short} as a {call_form.value} call",
            )
        if self.bindings.externally_bound(owner.module, short, *receivers):
            return VerifyResult(
                status=VerifyStatus.EXTERNALLY_BOUND,
                detail=(
                    f"{owner.module} binds "
                    f"{', '.join(r for r in receivers if r) or short} from outside the repo"
                ),
            )
        return VerifyResult(status=VerifyStatus.OK)

    def anchors(
        self, proposal: Proposal, *, row: UnresolvedRow | None
    ) -> tuple[Anchor, ...]:
        """One anchor per node the proposal depends on, hashed from the facts on disk (spec 5.5)."""
        src, dst = proposal.edge_pair()
        ids = [
            *(i for i in (src, dst, proposal.target.node_id) if i),
            *(row.resolution_path if row else ()),
        ]
        out: list[Anchor] = []
        for node_id in dict.fromkeys(ids):
            facts = self.files.get(node_id.split("::")[0])
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

        A path with no file on disk is a different answer than a file whose facts moved: telling an
        agent to rebuild the graph over a typo sends it around the loop again.
        """
        for path in self.paths_named(proposal, row):
            if path in self.missing:
                return VerifyResult(
                    status=VerifyStatus.NO_SUCH_PATH,
                    detail=f"{path} is not a file in this checkout",
                )
            facts = self.files.get(path)
            if facts is None or not facts.current:
                return VerifyResult(
                    status=VerifyStatus.STALE_FILE,
                    detail=(
                        f"{path} changed since the graph was built; "
                        "run `auditr graph build` first"
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
        facts = self.files.get(node_id.split("::")[0])
        return facts.node(node_id) if facts else None

    def _call_site(
        self,
        owner: GraphNode,
        forms: dict[tuple[str, CallForm], tuple[str | None, ...]],
        short: str,
        row: UnresolvedRow | None,
    ) -> tuple[CallForm, tuple[str | None, ...]]:
        """How the src really calls ``short``, and on which receiver roots.

        The queue row answers when there is one, because that is the row the proposal is answering;
        otherwise the src node's own facts answer, exactly the way the queue derived a row. The
        caller's ``payload.call_form`` is a claim and is never read here: a caller that said "bare"
        about an attribute call would buy itself the bare fact set and the receiver the
        externally-bound rule needs.
        """
        if row is not None:
            return row.call_form, (row.receiver_root,)
        return form_for(forms, short, owner.local_names)

    def _endpoint_kinds(self, owner: GraphNode, dst: str, kind: EdgeKind) -> str | None:
        """The reason the endpoints do not obey the resolver's kind rules, or ``None``."""
        src_kinds, dst_kinds = _ENDPOINT_KINDS[kind]
        if owner.kind not in src_kinds:
            return f"{owner.id} is a {owner.kind.value}, not one of {sorted(k.value for k in src_kinds)}"
        facts = self.files.get(dst.split("::")[0])
        node = facts.node(dst) if facts else None
        if node is None:
            return f"{dst} is not a node in its file"
        if node.kind not in dst_kinds:
            return f"{dst} is a {node.kind.value}, not one of {sorted(k.value for k in dst_kinds)}"
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
        return frozenset(owner.method_names)
