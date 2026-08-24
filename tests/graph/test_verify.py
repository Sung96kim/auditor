"""The AST-fact verifier (spec 9.2): the src node's own facts, re-read from disk."""

from pathlib import Path

import pytest

from auditor.graph.extract import extract_file_facts
from auditor.graph.hashes import file_hashes
from auditor.graph.model import CallForm, EdgeKind, NodeKind, UnresolvedRow
from auditor.graph.refine.models import (
    Proposal,
    RefinementKind,
    RefinementPayload,
    RefinementTarget,
)
from auditor.graph.refine.verify import FactVerifier, FileFacts, VerifyStatus
from auditor.graph.resolve_edges import NameBindings
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
