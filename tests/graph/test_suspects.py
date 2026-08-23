"""The `graph_unresolved` queue: which facts earn a row, in what form, at what priority.

S8 extends this file with the idle-drain tests; S2 owns the gating half.
"""

import pytest

from auditor.graph.extract import extract_file_facts
from auditor.graph.model import (
    CallForm,
    EdgeKind,
    FactKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    Resolution,
    UnresolvedReason,
)
from auditor.graph.resolve_edges import UnresolvedCollector, resolve_structural


def _rows(*files: tuple[str, str]):
    nodes = [
        n
        for path, src in files
        for n in extract_file_facts(path, src, "production").nodes
    ]
    return resolve_structural(nodes).unresolved


def _row(rows, node_id: str, name: str):
    hits = [r for r in rows if r.node_id == node_id and r.name == name]
    assert len(hits) == 1, [(r.node_id, r.fact_kind, r.name) for r in rows]
    return hits[0]


def test_a_name_with_no_repo_definer_gets_no_row():
    """Stdlib and third-party method names are the bulk of the raw misses; only names the repo
    actually defines are worth a model's time."""
    rows = _rows(("m.py", "import json\ndef f(x):\n    return json.dumps(x)\n"))
    assert [r.name for r in rows] == []


def test_a_test_only_definer_does_not_earn_a_production_row():
    """The role filter is the single biggest gate (cyclone 357 rows to 50)."""
    nodes = extract_file_facts(
        "svc.py", "def use():\n    return handle()\n", "production"
    ).nodes
    nodes += extract_file_facts(
        "test_x.py", "def handle():\n    return 1\n", "test"
    ).nodes
    assert resolve_structural(nodes).unresolved == []


def test_an_unimportable_bare_call_is_a_bare_row():
    rows = _rows(
        ("helper.py", "def handle():\n    return 1\n"),
        ("svc.py", "def use():\n    return handle()\n"),
    )
    row = _row(rows, "svc.py::use", "handle")
    assert row.fact_kind is FactKind.CALLEE
    assert row.call_form is CallForm.BARE
    assert row.receiver_root is None
    assert row.reason is UnresolvedReason.UNIMPORTABLE_NAME
    assert row.definers == ("helper.py::handle",)
    assert row.candidates == ()
    assert row.priority == 2
    assert row.externally_bound is False


def test_a_self_call_is_a_self_row():
    rows = _rows(
        ("other.py", "def helper():\n    return 1\n"),
        ("m.py", "class A:\n    def f(self):\n        return self.helper()\n"),
    )
    row = _row(rows, "m.py::A.f", "helper")
    assert row.fact_kind is FactKind.ATTR_CALLEE
    assert row.call_form is CallForm.SELF
    assert row.receiver_root == "self"
    assert row.priority == 2
    # `self.helper()` is also a typed call on class A; the self row is the one that survives
    assert [r for r in rows if r.fact_kind is FactKind.TYPED_CALL] == []


def test_a_chained_self_call_is_an_attr_row_not_a_self_row():
    """`self.dep.helper()` names the enclosing class only by accident, so it is an attribute call
    with receiver root `self` (17 of this repo's raw `self` rows were this shape)."""
    rows = _rows(
        ("other.py", "def helper():\n    return 1\n"),
        ("m.py", "class A:\n    def f(self):\n        return self.dep.helper()\n"),
    )
    row = _row(rows, "m.py::A.f", "helper")
    assert row.call_form is CallForm.ATTR
    assert row.receiver_root == "self"
    assert row.priority == 3


def test_a_name_called_both_bare_and_on_a_receiver_keeps_the_bare_row():
    """One row per name, and the bare form is the tractable one: a reader can settle `handle()`
    from the file alone, `job.handle()` needs the receiver's type."""
    rows = _rows(
        ("helper.py", "def handle():\n    return 1\n"),
        ("m.py", "def use(job):\n    return handle() or job.handle()\n"),
    )
    row = _row(rows, "m.py::use", "handle")
    assert row.call_form is CallForm.BARE
    assert row.priority == 2


def test_a_same_named_method_on_many_classes_is_an_attr_row():
    """`run` is defined on many repo classes; the receiver is a plain local, so the row carries
    the attribute form and drops to the attribute-form priority band."""
    rows = _rows(
        ("a.py", "class A:\n    def run(self):\n        return 1\n"),
        ("b.py", "class B:\n    def run(self):\n        return 2\n"),
        ("m.py", "def go(job):\n    return job.run()\n"),
    )
    row = _row(rows, "m.py::go", "run")
    assert row.fact_kind is FactKind.ATTR_CALLEE
    assert row.call_form is CallForm.ATTR
    assert row.receiver_root == "job"
    assert row.priority == 3


def test_two_reachable_definers_are_an_ambiguous_row():
    rows = _rows(
        ("a.py", "def save():\n    return 1\n"),
        ("b.py", "def save():\n    return 2\n"),
        ("caller.py", "import a\nimport b\ndef use():\n    return save()\n"),
    )
    row = _row(rows, "caller.py::use", "save")
    assert row.reason is UnresolvedReason.AMBIGUOUS_NAME
    assert set(row.candidates) == {"a.py::save", "b.py::save"}
    assert row.priority == 1


def test_a_non_repo_import_marks_the_row_externally_bound():
    """`re.search` and `subprocess.run` stay for display and are never briefed."""
    rows = _rows(
        ("util.py", "def search(pattern):\n    return pattern\n"),
        ("m.py", "import re\ndef f(s):\n    return re.search('x', s)\n"),
    )
    row = _row(rows, "m.py::f", "search")
    assert row.externally_bound is True
    assert row.receiver_root == "re"


def test_a_module_level_alias_of_an_external_object_is_externally_bound():
    """`_RX = re.compile(...)` hides `re` behind a module-level name; a call on `_RX` is still a
    call on a non-repo object."""
    rows = _rows(
        ("util.py", "def search(pattern):\n    return pattern\n"),
        (
            "m.py",
            "import re\n_RX = re.compile('x')\ndef f(s):\n    return _RX.search(s)\n",
        ),
    )
    row = _row(rows, "m.py::f", "search")
    assert row.receiver_root == "_RX"
    assert row.externally_bound is True


_OWN_BINDINGS = [
    ("positional", "def run(handler):\n    return handler()\n"),
    ("positional_only", "def run(handler, /):\n    return handler()\n"),
    ("keyword_only", "def run(*, handler):\n    return handler()\n"),
    ("kwonly_default", "def run(*, handler=None):\n    return handler()\n"),
    ("varargs", "def run(*handler):\n    return handler()\n"),
    ("kwargs", "def run(**handler):\n    return handler()\n"),
    (
        "except_as",
        "def run():\n    try:\n        pass\n"
        "    except Exception as handler:\n        return handler()\n",
    ),
    (
        "nested_def",
        "def run():\n    def handler():\n        return 1\n    return handler()\n",
    ),
    (
        "nested_def_param",
        "def run():\n    def inner(handler):\n        return handler()\n    return inner\n",
    ),
    ("lambda_param", "def run():\n    return lambda handler: handler()\n"),
    (
        "nested_class",
        "def run():\n    class handler:\n        pass\n    return handler()\n",
    ),
    ("local_import", "def run():\n    import handler\n    return handler()\n"),
    (
        "local_import_from",
        "def run():\n    from pkg import handler\n    return handler()\n",
    ),
    ("assigned", "def run():\n    handler = 1\n    return handler()\n"),
    ("for_target", "def run(items):\n    for handler in items:\n        handler()\n"),
    ("with_as", "def run(ctx):\n    with ctx as handler:\n        handler()\n"),
    ("walrus", "def run(ctx):\n    if (handler := ctx):\n        handler()\n"),
    ("tuple_unpack", "def run(pair):\n    handler, _ = pair\n    return handler()\n"),
]


@pytest.mark.parametrize(
    "src", [c[1] for c in _OWN_BINDINGS], ids=[c[0] for c in _OWN_BINDINGS]
)
def test_a_call_to_a_name_the_function_binds_gets_no_row(src: str):
    """The `is_hof` signal read the other way, across every binding form: `handler()` names the
    binding, whatever the repo happens to define under that name."""
    rows = _rows(
        ("helper.py", "def handler():\n    return 1\n"),
        ("models.py", "class handler:\n    pass\n"),
        ("m.py", src),
    )
    assert [r for r in rows if r.name == "handler"] == []


def test_a_class_reference_bound_as_a_local_gets_no_row():
    """A name the function assigns is that local, not the repo class of the same name."""
    rows = _rows(
        ("models.py", "class Widget:\n    pass\n"),
        ("m.py", "def make():\n    Widget = 1\n    return Widget\n"),
    )
    assert [r for r in rows if r.name == "Widget"] == []


def test_a_test_role_caller_never_earns_a_row():
    """The single biggest gate after the definer filter: test callers alone added 965 rows here."""
    nodes = extract_file_facts(
        "helper.py", "def handle():\n    return 1\n", "production"
    ).nodes
    nodes += extract_file_facts(
        "tests/test_x.py", "def test_it():\n    return handle()\n", "test"
    ).nodes
    assert resolve_structural(nodes).unresolved == []


def test_a_typed_call_on_a_non_repo_class_gets_no_row():
    """`str.lower` / `Path.mkdir` / pydantic receivers were 109 of 121 raw typed-call rows. The
    receiver type is known and known not to be a repo class, so the attribute row goes too: the
    call is settled, `util.lower` is simply not what it calls."""
    rows = _rows(
        ("util.py", "def lower(x):\n    return x\n"),
        ("m.py", "def f(s: str):\n    return s.lower()\n"),
    )
    assert rows == []


def test_a_typed_call_whose_receiver_chain_leaves_the_repo_gets_no_row():
    """The `_chain_in_repo` half of the same gate: `Svc` is a repo class, but its base is not, so
    the receiver is not fully in-repo and neither the typed row nor the attribute row survives."""
    rows = _rows(
        ("svc.py", "from pydantic import BaseModel\nclass Svc(BaseModel):\n    pass\n"),
        ("other.py", "def do_thing():\n    return 1\n"),
        ("m.py", "from svc import Svc\ndef f(s: Svc):\n    return s.do_thing()\n"),
    )
    assert [r for r in rows if r.name == "do_thing"] == []


def test_a_receiver_class_the_caller_cannot_import_still_earns_its_row():
    """The other side of that gate: an unresolvable receiver type is unknown, not known-non-repo,
    so the attribute row survives. This is exactly the miss the queue exists for."""
    rows = _rows(
        ("svc.py", "class Svc:\n    pass\n"),
        ("other.py", "def do_thing():\n    return 1\n"),
        ("m.py", "def f(s: Svc):\n    return s.do_thing()\n"),
    )
    row = _row(rows, "m.py::f", "do_thing")
    assert row.fact_kind is FactKind.ATTR_CALLEE
    assert row.call_form is CallForm.ATTR


def test_a_name_already_edged_from_the_node_gets_no_row():
    """11 % of raw rows were already resolved through the typed-call path; a row that duplicates
    an edge the node already has is noise."""
    rows = _rows(
        (
            "svc/foo.py",
            "class FooService:\n    def do_thing(self, p):\n        return 1\n",
        ),
        (
            "svc/bar.py",
            "class BarService:\n    def do_thing(self, p):\n        return 2\n",
        ),
        ("svc/__init__.py", "from .foo import *\nfrom .bar import *\n"),
        (
            "routes.py",
            "from svc import FooService\n"
            "def handler(payload, svc: FooService = Depends(FooService)):\n"
            "    return svc.do_thing(payload)\n",
        ),
    )
    assert [r for r in rows if r.name == "do_thing"] == []


def test_an_unresolved_class_reference_is_a_class_ref_row():
    rows = _rows(
        ("models.py", "class Widget:\n    pass\n"),
        ("m.py", "def uses() -> int:\n    Widget()\n    return 0\n"),
    )
    row = _row(rows, "m.py::uses", "Widget")
    assert row.fact_kind is FactKind.CLASS_REF
    assert row.call_form is CallForm.BARE


def test_rows_are_unique_per_node_name_and_reason():
    """The table's primary key; a repeated call must not produce a second row."""
    rows = _rows(
        ("helper.py", "def handle():\n    return 1\n"),
        ("svc.py", "def use():\n    handle()\n    return handle()\n"),
    )
    keys = [(r.node_id, r.name, r.reason) for r in rows]
    assert len(keys) == len(set(keys))


def test_a_typed_call_row_replaces_the_attribute_row_for_the_same_name():
    """Both facts describe one call site; the typed row names the declared receiver class, which
    is what a refiner needs, so it is the one kept."""
    rows = _rows(
        ("base.py", "class Base:\n    pass\n"),
        ("svc.py", "from base import Base\nclass Svc(Base):\n    pass\n"),
        ("other.py", "def do_thing():\n    return 1\n"),
        ("m.py", "from svc import Svc\ndef f(s: Svc):\n    return s.do_thing()\n"),
    )
    row = _row(rows, "m.py::f", "do_thing")
    assert row.fact_kind is FactKind.TYPED_CALL
    assert row.receiver_root == "Svc"


def test_a_settled_receiver_does_not_silence_the_same_call_on_another_receiver():
    """The suppression is keyed by receiver, not by method name: `p: Path` settling `p.run()`
    must leave the genuine `job.run()` miss in the queue (`run`, `get`, `load` are exactly the
    names a stdlib type and a repo class share)."""
    rows = _rows(
        ("a.py", "class A:\n    def run(self):\n        return 1\n"),
        ("b.py", "class B:\n    def run(self):\n        return 2\n"),
        (
            "m.py",
            "from pathlib import Path\n"
            "def f(p: Path, job):\n"
            "    p.run()\n"
            "    return job.run()\n",
        ),
    )
    row = _row(rows, "m.py::f", "run")
    assert row.call_form is CallForm.ATTR
    assert row.receiver_root == "job"


def test_a_bound_bare_name_falls_back_to_the_attribute_form():
    """`handle()` names the parameter, but `job.handle()` beside it is still a real miss: the
    form choice skips the bound form instead of dropping the row."""
    rows = _rows(
        ("a.py", "class A:\n    def handle(self):\n        return 1\n"),
        ("m.py", "def f(job, handle):\n    handle()\n    return job.handle()\n"),
    )
    row = _row(rows, "m.py::f", "handle")
    assert row.call_form is CallForm.ATTR
    assert row.receiver_root == "job"


def _collector() -> UnresolvedCollector:
    return UnresolvedCollector(
        bindings_by_module={}, aliases_by_module={}, dotted_to_id={}
    )


def _caller(**kw) -> GraphNode:
    return GraphNode(
        id="m.py::f",
        kind=NodeKind.FUNCTION,
        name="f",
        module="m.py",
        qualname="f",
        role="production",
        **kw,
    )


def _resolution() -> Resolution:
    return Resolution(
        definers=("helper.py::handle",), reason=UnresolvedReason.UNIMPORTABLE_NAME
    )


def test_collector_drops_a_row_the_node_already_has_an_edge_for():
    """The collector applies the post-pass gates on its own: no resolver needed to state that a
    row duplicating an edge the node already has is noise."""
    c = _collector()
    c.note(
        _caller(),
        _resolution(),
        fact_kind=FactKind.CALLEE,
        name="handle",
        receiver_roots=(None,),
        call_form=CallForm.BARE,
    )
    edges = [
        GraphEdge(src="m.py::f", dst="helper.py::handle", kind=EdgeKind.CALLS),
    ]
    assert c.drain(edges) == []
    assert len(c.drain([])) == 1


def test_collector_drops_only_the_settled_receiver():
    """The second gate, keyed by receiver: settling `p.run` leaves `job.run` alone."""
    c = _collector()
    for root in ("p", "job"):
        c.note(
            _caller(),
            _resolution(),
            fact_kind=FactKind.ATTR_CALLEE,
            name="run",
            receiver_roots=(root,),
            call_form=CallForm.ATTR,
        )
    c.settle("m.py::f", "p", "run")
    (row,) = c.drain([])
    assert row.receiver_root == "job"


def test_collector_skips_a_bare_name_the_node_binds():
    c = _collector()
    c.note(
        _caller(local_names=("handle",)),
        _resolution(),
        fact_kind=FactKind.CALLEE,
        name="handle",
        receiver_roots=(None,),
        call_form=CallForm.BARE,
    )
    assert c.drain([]) == []
