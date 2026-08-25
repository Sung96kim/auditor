"""The AST-fact verifier (spec 9.2): the src node's own facts, re-read from disk."""

from pathlib import Path

import pytest

from auditor.graph.extract import extract_file_facts
from auditor.graph.hashes import file_hashes
from auditor.graph.model import CallForm, EdgeKind, NodeKind, UnresolvedRow
from auditor.graph.refine.models import (
    REFINABLE_EDGE_KINDS,
    Proposal,
    RefinementKind,
    RefinementPayload,
    RefinementTarget,
)
from auditor.graph.refine.verify import (
    _ENDPOINT_KINDS,
    FactVerifier,
    FileFacts,
    VerifyStatus,
)
from auditor.graph.resolve_edges import NameBindings, resolve_structural
from auditor.roles import RoleClassifier

CALLER = "from helper import unrelated\n\n\ndef main():\n    return read_event()\n"
HELPER = "def read_event():\n    return {}\n\n\ndef unrelated():\n    return 1\n"
EXTERNAL_CALLER = "import re\n\n\ndef main():\n    return re.search('x', 'y')\n"
# one bare call and one attribute call on a repo-imported receiver, from the same function
MIXED_CALLER = (
    "from helper import read_event, store\n\n\n"
    "def main():\n    read_event()\n    return store.unrelated()\n"
)
MIXED_HELPER = (
    "def read_event():\n    return {}\n\n\n"
    "def unrelated():\n    return 1\n\n\nstore = object()\n"
)
SELF_CALLER = (
    "class Runner:\n"
    "    def run(self):\n        return self.step()\n\n"
    "    def step(self):\n        return 1\n"
)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, source in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(source)


def _verifier(
    root: Path,
    files: dict[str, str],
    *,
    stale: str | None = None,
    missing: tuple[str, ...] = (),
) -> FactVerifier:
    """A verifier holding the fresh facts for every file, with ``stale``'s cached hash faked to a
    value the file no longer produces and ``missing`` naming paths with no file on disk."""
    roles = RoleClassifier()
    facts = {}
    modules = []
    for rel, source in files.items():
        nodes = extract_file_facts(rel, source, roles.classify(rel, source).value).nodes
        cached = "0" * 64 if rel == stale else file_hashes(nodes).truth
        facts[rel] = FileFacts.of(root, rel, cached, roles)
        modules += [n for n in nodes if n.kind is NodeKind.MODULE]
    return FactVerifier(
        files=facts, bindings=NameBindings.of(modules), missing=frozenset(missing)
    )


def _add_edge(
    src: str,
    dst: str,
    name: str,
    kind: EdgeKind = EdgeKind.CALLS,
    call_form: CallForm | None = None,
) -> Proposal:
    """``call_form`` is what a caller *claims*. The verifier reads the queue row, or the src
    node's own facts, and never this field."""
    return Proposal(
        kind=RefinementKind.ADD_EDGE,
        target=RefinementTarget(src=src, dst=dst, edge_kind=kind, name=name),
        payload=RefinementPayload(call_form=call_form),
        reason="the bare call resolves inside the package",
    )


def _row(node_id: str, name: str, definers: tuple[str, ...], **kw) -> UnresolvedRow:
    return UnresolvedRow(
        node_id=node_id,
        fact_kind="callee",
        name=name,
        reason="unimportable_name",
        definers=definers,
        **kw,
    )


def test_a_bare_call_backed_by_the_facts_verifies(tmp_path: Path):
    files = {"caller.py": CALLER, "helper.py": HELPER}
    _write(tmp_path, files)
    verifier = _verifier(tmp_path, files)
    result = verifier.check(
        _add_edge("caller.py::main", "helper.py::read_event", "read_event"),
        row=_row("caller.py::main", "read_event", ("helper.py::read_event",)),
        definers=("helper.py::read_event",),
    )
    assert result.status is VerifyStatus.OK
    assert result.checked is True


def test_a_name_the_src_never_calls_is_rejected(tmp_path: Path):
    files = {"caller.py": CALLER, "helper.py": HELPER}
    _write(tmp_path, files)
    verifier = _verifier(tmp_path, files)
    result = verifier.check(
        _add_edge("caller.py::main", "helper.py::unrelated", "unrelated"),
        row=_row("caller.py::main", "unrelated", ("helper.py::unrelated",)),
        definers=("helper.py::unrelated",),
    )
    assert result.status is VerifyStatus.NO_FACT


def test_an_externally_bound_name_is_rejected(tmp_path: Path):
    files = {
        "caller.py": EXTERNAL_CALLER,
        "helper.py": "def search(p, s):\n    return None\n",
    }
    _write(tmp_path, files)
    verifier = _verifier(tmp_path, files)
    result = verifier.check(
        _add_edge("caller.py::main", "helper.py::search", "search"),
        row=_row(
            "caller.py::main",
            "search",
            ("helper.py::search",),
            call_form=CallForm.ATTR,
            receiver_root="re",
        ),
        definers=("helper.py::search",),
    )
    assert result.status is VerifyStatus.EXTERNALLY_BOUND


def test_a_destination_outside_the_definers_is_rejected(tmp_path: Path):
    files = {"caller.py": CALLER, "helper.py": HELPER}
    _write(tmp_path, files)
    verifier = _verifier(tmp_path, files)
    result = verifier.check(
        _add_edge("caller.py::main", "helper.py::unrelated", "read_event"),
        row=_row("caller.py::main", "read_event", ("helper.py::read_event",)),
        definers=("helper.py::read_event",),
    )
    assert result.status is VerifyStatus.NOT_A_DEFINER


def test_a_file_that_moved_since_the_build_is_rejected(tmp_path: Path):
    files = {"caller.py": CALLER, "helper.py": HELPER}
    _write(tmp_path, files)
    verifier = _verifier(tmp_path, files, stale="caller.py")
    result = verifier.check(
        _add_edge("caller.py::main", "helper.py::read_event", "read_event"),
        row=_row("caller.py::main", "read_event", ("helper.py::read_event",)),
        definers=("helper.py::read_event",),
    )
    assert result.status is VerifyStatus.STALE_FILE
    assert "caller.py" in result.detail


def test_a_missing_src_node_is_rejected(tmp_path: Path):
    files = {"caller.py": CALLER, "helper.py": HELPER}
    _write(tmp_path, files)
    verifier = _verifier(tmp_path, files)
    result = verifier.check(
        _add_edge("caller.py::gone", "helper.py::read_event", "read_event"),
        row=None,
        definers=("helper.py::read_event",),
    )
    assert result.status is VerifyStatus.NO_SRC_NODE


def test_a_bare_row_for_an_attribute_call_has_no_fact(tmp_path: Path):
    """The regression for the call-form split: `unrelated` is only ever called as
    `store.unrelated()`, so the bare fact set must not contain it."""
    files = {"caller.py": MIXED_CALLER, "helper.py": MIXED_HELPER}
    _write(tmp_path, files)
    result = _verifier(tmp_path, files).check(
        _add_edge("caller.py::main", "helper.py::unrelated", "unrelated"),
        row=_row(
            "caller.py::main",
            "unrelated",
            ("helper.py::unrelated",),
            call_form=CallForm.BARE,
        ),
        definers=("helper.py::unrelated",),
    )
    assert result.status is VerifyStatus.NO_FACT
    assert "bare" in result.detail


def test_the_attribute_row_for_the_same_call_verifies(tmp_path: Path):
    """The control for the case above: on its own form, and on a receiver the repo defines, the
    same call passes."""
    files = {"caller.py": MIXED_CALLER, "helper.py": MIXED_HELPER}
    _write(tmp_path, files)
    result = _verifier(tmp_path, files).check(
        _add_edge("caller.py::main", "helper.py::unrelated", "unrelated"),
        row=_row(
            "caller.py::main",
            "unrelated",
            ("helper.py::unrelated",),
            call_form=CallForm.ATTR,
            receiver_root="store",
        ),
        definers=("helper.py::unrelated",),
    )
    assert result.status is VerifyStatus.OK


def test_a_direct_self_receiver_verifies_as_the_self_form(tmp_path: Path):
    """`self` means a direct `self` or `cls` receiver, which is what `call_forms` decides; a
    chained `self.a.b()` is an attribute call."""
    files = {"runner.py": SELF_CALLER}
    _write(tmp_path, files)
    result = _verifier(tmp_path, files).check(
        _add_edge("runner.py::Runner.run", "runner.py::Runner.step", "step"),
        row=_row(
            "runner.py::Runner.run",
            "step",
            ("runner.py::Runner.step",),
            call_form=CallForm.SELF,
            receiver_root="self",
        ),
        definers=("runner.py::Runner.step",),
    )
    assert result.status is VerifyStatus.OK


@pytest.mark.parametrize("claimed", [None, CallForm.BARE])
def test_an_external_receiver_is_rejected_with_no_queue_row(
    tmp_path: Path, claimed: CallForm | None
):
    """With no row the receiver root comes from the src node's own facts, and the caller's own
    `call_form` never buys it the bare fact set."""
    files = {
        "caller.py": EXTERNAL_CALLER,
        "helper.py": "def search(p, s):\n    return None\n",
    }
    _write(tmp_path, files)
    result = _verifier(tmp_path, files).check(
        _add_edge("caller.py::main", "helper.py::search", "search", call_form=claimed),
        row=None,
        definers=("helper.py::search",),
    )
    assert result.status is VerifyStatus.EXTERNALLY_BOUND
    assert "binds re " in result.detail  # the receiver root, not just the method name


def test_a_path_that_never_existed_is_not_reported_as_stale(tmp_path: Path):
    """A file that does not exist earns its own status: `rebuild the graph` is the wrong
    instruction, and the agent needs to know the path is simply wrong."""
    files = {"caller.py": CALLER, "helper.py": HELPER}
    _write(tmp_path, files)
    result = _verifier(tmp_path, files, missing=("ghost.py",)).check(
        _add_edge("caller.py::main", "ghost.py::read_event", "read_event"),
        row=None,
        definers=("ghost.py::read_event",),
    )
    assert result.status is VerifyStatus.NO_SUCH_PATH
    assert "ghost.py" in result.detail


@pytest.mark.parametrize(
    "kind",
    [
        RefinementKind.CONFIRM_EDGE,
        RefinementKind.ANNOTATE_NODE,
        RefinementKind.UNRESOLVABLE,
        RefinementKind.RELABEL_CLUSTER,
    ],
)
def test_the_kinds_with_no_verifier_report_unverified(
    tmp_path: Path, kind: RefinementKind
):
    files = {"caller.py": CALLER, "helper.py": HELPER}
    _write(tmp_path, files)
    targets = {
        RefinementKind.CONFIRM_EDGE: RefinementTarget(
            src="caller.py::main",
            dst="helper.py::read_event",
            edge_kind=EdgeKind.CALLS,
            name="read_event",
        ),
        RefinementKind.ANNOTATE_NODE: RefinementTarget(node_id="caller.py::main"),
        RefinementKind.UNRESOLVABLE: RefinementTarget(
            node_id="caller.py::main", name="read_event"
        ),
        RefinementKind.RELABEL_CLUSTER: RefinementTarget(members=("caller.py::main",)),
    }
    payloads = {
        RefinementKind.ANNOTATE_NODE: RefinementPayload(
            annotation="the hook entry point"
        ),
        RefinementKind.RELABEL_CLUSTER: RefinementPayload(label="hooks"),
        RefinementKind.UNRESOLVABLE: RefinementPayload(reason_code="dynamic"),
    }
    result = _verifier(tmp_path, files).check(
        Proposal(
            kind=kind,
            target=targets[kind],
            payload=payloads.get(kind, RefinementPayload()),
            reason="checked by hand",
        ),
        row=None,
        definers=(),
    )
    assert result.status is VerifyStatus.UNVERIFIED
    assert result.accepted is True
    assert result.checked is False


def test_anchors_cover_the_endpoints_and_the_resolution_path(tmp_path: Path):
    files = {"caller.py": CALLER, "helper.py": HELPER}
    _write(tmp_path, files)
    verifier = _verifier(tmp_path, files)
    anchors = verifier.anchors(
        _add_edge("caller.py::main", "helper.py::read_event", "read_event"),
        row=_row(
            "caller.py::main",
            "read_event",
            ("helper.py::read_event",),
            resolution_path=("helper.py",),
        ),
    )
    assert {a.node_id for a in anchors} == {
        "caller.py::main",
        "helper.py::read_event",
        "helper.py",
    }
    assert all(len(a.truth_sha) == 64 and a.file_sha for a in anchors)


#: one repo carrying all five refinable edge kinds, plus same-named decoys of the wrong node kind
SHAPES = (
    "class Box:\n    def area(self):\n        return 1\n\n"
    "    def size(self):\n        return 2\n\n\n"
    "class Crate:\n    def area(self):\n        return 3\n"
)
FUNCS = "def Box():\n    return 1\n\n\ndef area():\n    return 0\n"
KIDS = "from shapes import Box\n\n\nclass Kid(Box):\n    def area(self):\n        return 3\n"
USERS = (
    "from shapes import Box\nfrom helper import read_event, unrelated\n\n\n"
    "def main(box: Box):\n    register(read_event)\n    return read_event()\n"
)
KINDS_REPO = {
    "shapes.py": SHAPES,
    "funcs.py": FUNCS,
    "kids.py": KIDS,
    "users.py": USERS,
    "helper.py": HELPER,
}


def test_the_endpoint_table_covers_exactly_the_kinds_a_proposal_may_name():
    """`_ENDPOINT_KINDS[kind]` is a bare lookup, so a sixth refinable kind upstream would raise
    here rather than answer."""
    assert set(_ENDPOINT_KINDS) == REFINABLE_EDGE_KINDS


def test_the_endpoint_table_says_what_the_resolver_emits(tmp_path: Path):
    """The table is checked against real resolver output, not against its own comment: `overrides`
    runs method to method, which is what `_class_edges` builds."""
    _write(tmp_path, KINDS_REPO)
    roles = RoleClassifier()
    nodes = [
        node
        for rel, source in KINDS_REPO.items()
        for node in extract_file_facts(
            rel, source, roles.classify(rel, source).value
        ).nodes
    ]
    by_id = {n.id: n for n in nodes}
    emitted = {
        e.kind for e in resolve_structural(nodes).edges if e.kind in _ENDPOINT_KINDS
    }
    assert EdgeKind.OVERRIDES in emitted  # the row the comment used to get wrong
    for edge in resolve_structural(nodes).edges:
        if edge.kind not in _ENDPOINT_KINDS:
            continue
        src_kinds, dst_kinds = _ENDPOINT_KINDS[edge.kind]
        assert by_id[edge.src].kind in src_kinds, edge
        assert by_id[edge.dst].kind in dst_kinds, edge


@pytest.mark.parametrize(
    ("kind", "src", "dst", "name", "status"),
    [
        (
            EdgeKind.CALLS,
            "users.py::main",
            "helper.py::read_event",
            "read_event",
            VerifyStatus.OK,
        ),
        (
            EdgeKind.CALLS,
            "users.py::main",
            "helper.py::unrelated",
            "unrelated",
            VerifyStatus.NO_FACT,
        ),
        (
            EdgeKind.REFERENCES_TYPE,
            "users.py::main",
            "shapes.py::Box",
            "Box",
            VerifyStatus.OK,
        ),
        (
            EdgeKind.REFERENCES_TYPE,
            "users.py::main",
            "shapes.py::Crate",
            "Crate",
            VerifyStatus.NO_FACT,
        ),
        (
            EdgeKind.CALLBACK_ARG,
            "users.py::main",
            "helper.py::read_event",
            "read_event",
            VerifyStatus.OK,
        ),
        (
            EdgeKind.CALLBACK_ARG,
            "users.py::main",
            "helper.py::unrelated",
            "unrelated",
            VerifyStatus.NO_FACT,
        ),
        (EdgeKind.INHERITS, "kids.py::Kid", "shapes.py::Box", "Box", VerifyStatus.OK),
        (
            EdgeKind.INHERITS,
            "kids.py::Kid",
            "shapes.py::Crate",
            "Crate",
            VerifyStatus.NO_FACT,
        ),
        (
            EdgeKind.OVERRIDES,
            "kids.py::Kid.area",
            "shapes.py::Box.area",
            "area",
            VerifyStatus.OK,
        ),
        (
            EdgeKind.OVERRIDES,
            "kids.py::Kid.area",
            "shapes.py::Box.size",
            "size",
            VerifyStatus.NO_FACT,
        ),
    ],
)
def test_every_edge_kind_is_checked_against_its_own_fact_tuple(
    tmp_path: Path, kind: EdgeKind, src: str, dst: str, name: str, status: VerifyStatus
):
    _write(tmp_path, KINDS_REPO)
    result = _verifier(tmp_path, KINDS_REPO).check(
        _add_edge(src, dst, name, kind=kind), row=None, definers=(dst,)
    )
    assert result.status is status, result.detail
    if status is VerifyStatus.NO_FACT and kind is not EdgeKind.CALLS:
        assert "as a" not in result.detail  # only `calls` picks a tuple by call form


@pytest.mark.parametrize(
    ("kind", "src", "dst", "name", "reason"),
    [
        (
            EdgeKind.REFERENCES_TYPE,
            "shapes.py::Box",
            "shapes.py::Crate",
            "Crate",
            "is a class",
        ),
        (EdgeKind.INHERITS, "kids.py::Kid", "funcs.py::Box", "Box", "is a function"),
        (
            EdgeKind.OVERRIDES,
            "kids.py::Kid.area",
            "funcs.py::area",
            "area",
            "is a function",
        ),
        (
            EdgeKind.REFERENCES_TYPE,
            "users.py::main",
            "shapes.py::Ghost",
            "Ghost",
            "is not a node in its file",
        ),
    ],
)
def test_endpoints_that_break_the_resolvers_kind_rules_are_refused(
    tmp_path: Path, kind: EdgeKind, src: str, dst: str, name: str, reason: str
):
    _write(tmp_path, KINDS_REPO)
    result = _verifier(tmp_path, KINDS_REPO).check(
        _add_edge(src, dst, name, kind=kind), row=None, definers=(dst,)
    )
    assert result.status is VerifyStatus.BAD_NODE_KIND
    assert reason in result.detail


def test_a_source_that_is_not_a_method_overrides_nothing(tmp_path: Path):
    _write(tmp_path, KINDS_REPO)
    result = _verifier(tmp_path, KINDS_REPO).check(
        _add_edge(
            "users.py::main", "shapes.py::Box.area", "area", kind=EdgeKind.OVERRIDES
        ),
        row=None,
        definers=("shapes.py::Box.area",),
    )
    assert result.status is VerifyStatus.BAD_NODE_KIND
    assert "overrides nothing" in result.detail


def _resolve_ambiguous(candidate: str, name: str = "read_event") -> Proposal:
    return Proposal(
        kind=RefinementKind.RESOLVE_AMBIGUOUS,
        target=RefinementTarget(
            node_id="users.py::main", name=name, edge_kind=EdgeKind.CALLS
        ),
        payload=RefinementPayload(candidate=candidate),
        reason="the import pins it to the helper",
    )


@pytest.mark.parametrize(
    ("candidates", "status"),
    [
        (("helper.py::read_event",), VerifyStatus.OK),
        (("other.py::read_event",), VerifyStatus.NOT_A_DEFINER),
    ],
)
def test_resolve_ambiguous_may_only_choose_from_the_gated_candidates(
    tmp_path: Path, candidates: tuple[str, ...], status: VerifyStatus
):
    """Spec 9.2 requires `candidate_id` to be in `candidates_json`, which is narrower than the
    role-filtered definers: a definer outside the gated set is not an answer to this row."""
    _write(tmp_path, KINDS_REPO)
    row = _row(
        "users.py::main",
        "read_event",
        ("helper.py::read_event", "other.py::read_event"),
        candidates=candidates,
    )
    result = _verifier(tmp_path, KINDS_REPO).check(
        _resolve_ambiguous("helper.py::read_event"),
        row=row,
        definers=("helper.py::read_event", "other.py::read_event"),
    )
    assert result.status is status
    if status is VerifyStatus.NOT_A_DEFINER:
        assert "candidates" in result.detail


BOUND_CALLER = "def run(handler):\n    return handler()\n"
BOUND_HELPER = "def handler():\n    return 1\n"


def test_a_bare_name_the_source_binds_itself_is_no_fact(tmp_path: Path):
    """The queue drops a bare name the node binds itself, so the no-row path must drop it too:
    `handler` here is the parameter, not the repo function of the same name."""
    files = {"a.py": BOUND_CALLER, "b.py": BOUND_HELPER}
    _write(tmp_path, files)
    result = _verifier(tmp_path, files).check(
        _add_edge("a.py::run", "b.py::handler", "handler"),
        row=None,
        definers=("b.py::handler",),
    )
    assert result.status is VerifyStatus.NO_FACT
    assert "binds handler itself" in result.detail


def test_the_queue_drops_the_same_bare_name_the_verifier_does(tmp_path: Path):
    """The parity claim, measured: the resolver produces no row for the shape above, so a verifier
    that accepted it would be answering a question the queue never asked."""
    files = {"a.py": BOUND_CALLER, "b.py": BOUND_HELPER}
    roles = RoleClassifier()
    nodes = [
        node
        for rel, source in files.items()
        for node in extract_file_facts(
            rel, source, roles.classify(rel, source).value
        ).nodes
    ]
    rows = resolve_structural(nodes).unresolved
    assert [r for r in rows if r.node_id == "a.py::run" and r.name == "handler"] == []


EXTERNAL_TABLE = "from rich.table import Table\n\n\ndef main():\n    return Table()\n"
REPO_TABLE = "class Table:\n    def add(self):\n        return 1\n"


def test_an_externally_bound_name_beats_the_endpoint_kind(tmp_path: Path):
    """The collision control is built from rows exactly like this one, and it grades a runner on
    answering `unresolvable`; a node-kind message steers it away from that verdict."""
    files = {"caller.py": EXTERNAL_TABLE, "base.py": REPO_TABLE}
    _write(tmp_path, files)
    result = _verifier(tmp_path, files).check(
        _add_edge("caller.py::main", "base.py::Table", "Table"),
        row=None,
        definers=("base.py::Table",),
    )
    assert result.status is VerifyStatus.EXTERNALLY_BOUND
    assert "rich" not in result.detail and "binds Table" in result.detail


def test_a_path_the_caller_never_loaded_is_not_reported_as_stale(tmp_path: Path):
    """Rebuilding the graph cannot fix a file the caller forgot to hand in, so the two answers
    are kept apart."""
    files = {"caller.py": CALLER, "helper.py": HELPER}
    _write(tmp_path, files)
    verifier = FactVerifier(
        files={"caller.py": _verifier(tmp_path, files).files["caller.py"]}
    )
    result = verifier.check(
        _add_edge("caller.py::main", "helper.py::read_event", "read_event"),
        row=None,
        definers=("helper.py::read_event",),
    )
    assert result.status is VerifyStatus.NOT_LOADED
    assert "helper.py" in result.detail
    assert "graph build" not in result.detail


@pytest.mark.parametrize(
    ("kind", "target", "payload"),
    [
        (
            RefinementKind.RELABEL_CLUSTER,
            RefinementTarget(members=("caller.py::main", "helper.py::read_event")),
            RefinementPayload(label="ingest"),
        ),
        (
            RefinementKind.MOVE_NODE,
            RefinementTarget(
                node_id="caller.py::main", members=("helper.py::read_event",)
            ),
            RefinementPayload(),
        ),
    ],
)
def test_a_cluster_kind_anchors_every_member_it_names(
    tmp_path: Path,
    kind: RefinementKind,
    target: RefinementTarget,
    payload: RefinementPayload,
):
    """ "One anchor per node the proposal depends on" has to include the members, or a cluster
    kind is pinned to nothing at all."""
    files = {"caller.py": CALLER, "helper.py": HELPER}
    _write(tmp_path, files)
    proposal = Proposal(
        kind=kind, target=target, payload=payload, reason="the members are one concept"
    )
    assert set(FactVerifier.paths_named(proposal, None)) == {"caller.py", "helper.py"}
    anchors = _verifier(tmp_path, files).anchors(proposal, row=None)
    assert {a.node_id for a in anchors} == {"caller.py::main", "helper.py::read_event"}
