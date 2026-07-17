import pytest

from auditor.graph.extract import extract_file_facts
from auditor.graph.model import GraphEdge
from auditor.graph.resolve_edges import resolve_structural

SRC_A = """
class Base:
    def run(self): ...

class Impl(Base):
    def run(self, ctx: Request):
        return self.helper()

def helper():
    return 1
"""


def _edges(*sources: str) -> list[GraphEdge]:
    nodes = []
    for i, s in enumerate(sources):
        nodes += extract_file_facts(f"m{i}.py", s, "production").nodes
    return resolve_structural(nodes)


def _pairs(edges: list[GraphEdge], kind: str) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in edges if e.kind == kind}


def test_override_links_impl_to_base_method():
    edges = _edges(SRC_A)
    assert ("m0.py::Impl.run", "m0.py::Base.run") in _pairs(edges, "overrides")


def test_inherits_edge():
    assert ("m0.py::Impl", "m0.py::Base") in _pairs(_edges(SRC_A), "inherits")


def test_calls_resolved_by_name():
    # Impl.run calls self.helper() -> resolves to the module-level helper by short name
    assert ("m0.py::Impl.run", "m0.py::helper") in _pairs(_edges(SRC_A), "calls")


def test_cross_module_call_resolves_only_via_import():
    """A call site gives only the name (no receiver type), so cross-module resolution uses the
    import graph as the disambiguator: a name resolves only to a candidate whose module the
    caller actually imports. This kills the from_orm/get over-match (a `dict.get()` caller that
    never imports the service no longer edges to SubmissionFieldsService.get)."""
    edges = _edges(
        "from m1 import save\ndef use():\n    save()\n    other()\n",  # m0 imports only m1
        "def save():\n    return 1\n",  # m1 — imported
        "def save():\n    return 2\n",  # m2 — also defines save, NOT imported
        "def other():\n    return 3\n",  # m3 — not imported
    )
    calls = _pairs(edges, "calls")
    assert ("m0.py::use", "m1.py::save") in calls  # import disambiguates to m1
    assert ("m0.py::use", "m2.py::save") not in calls  # not imported
    assert ("m0.py::use", "m3.py::other") not in calls  # not imported → no edge


def _reexport_nodes(*files: tuple[str, str]) -> list:
    out = []
    for path, src in files:
        out += extract_file_facts(path, src, "production").nodes
    return out


def test_reexport_resolves_through_package_init():
    """A caller importing a package resolves a symbol the package __init__ re-exports from a
    leaf module. Re-export following is always on: it can only recover a true edge or drop a
    genuinely-ambiguous one (the len==1 gate never invents a wrong edge)."""
    files = (
        ("caller.py", "from pkg import handle\ndef use():\n    return handle()\n"),
        (
            "pkg/__init__.py",
            "from pkg.leaf import handle\n",
        ),  # __init__ re-exports the leaf
        ("pkg/leaf.py", "def handle():\n    return 1\n"),
    )
    calls = _pairs(resolve_structural(_reexport_nodes(*files)), "calls")
    assert ("caller.py::use", "pkg/leaf.py::handle") in calls


def test_star_reexport_reference_edge():
    """A class imported through a plain-module star aggregator (`from .real import *` in
    schemas.py, then `from pkg.schemas import Widget` in the consumer) resolves to its DEFINING
    module. Finding 1."""
    files = (
        ("pkg/real.py", "class Widget:\n    pass\n"),
        ("pkg/schemas.py", "from .real import *\n"),
        (
            "pkg/consumer.py",
            "from pkg.schemas import Widget\ndef q() -> int:\n    Widget()\n    return 0\n",
        ),
    )
    refs = _pairs(resolve_structural(_reexport_nodes(*files)), "references_type")
    assert ("pkg/consumer.py::q", "pkg/real.py::Widget") in refs


def test_transitive_star_reexport_reference_edge():
    """Two star hops (consumer → pkg/__init__ → schemas → real) resolve transitively — the
    star relation is namespace inclusion, followed to a fixpoint."""
    files = (
        ("pkg/real.py", "class Widget:\n    pass\n"),
        ("pkg/schemas.py", "from .real import *\n"),
        ("pkg/__init__.py", "from .schemas import *\n"),
        (
            "pkg/consumer.py",
            "from pkg import Widget\ndef q() -> int:\n    Widget()\n    return 0\n",
        ),
    )
    refs = _pairs(resolve_structural(_reexport_nodes(*files)), "references_type")
    assert ("pkg/consumer.py::q", "pkg/real.py::Widget") in refs


def test_star_reexport_call_edge():
    """The re-export fix threads through call resolution too (shared import gate): a function
    reached via a star aggregator resolves to its defining module — relevant to the
    Depends-injected-service call gap (Finding 2)."""
    files = (
        ("pkg/impl.py", "def handle():\n    return 1\n"),
        ("pkg/api.py", "from .impl import *\n"),
        (
            "pkg/consumer.py",
            "from pkg.api import handle\ndef use():\n    return handle()\n",
        ),
    )
    calls = _pairs(resolve_structural(_reexport_nodes(*files)), "calls")
    assert ("pkg/consumer.py::use", "pkg/impl.py::handle") in calls


def test_plain_module_explicit_import_not_followed_as_reexport():
    """Precision boundary: a plain (non-__init__) module's *explicit* import is not treated as a
    re-export surface — only star imports propagate reachability, so consumers of the plain
    module do not transitively reach its internal dependency (guards the false hairball)."""
    files = (
        ("pkg/internal.py", "class Secret:\n    pass\n"),
        ("pkg/svc.py", "from .internal import Secret\n"),  # explicit, NOT star
        (
            "pkg/consumer.py",
            "from pkg.svc import Secret\ndef q() -> int:\n    Secret()\n    return 0\n",
        ),
    )
    refs = _pairs(resolve_structural(_reexport_nodes(*files)), "references_type")
    assert ("pkg/consumer.py::q", "pkg/internal.py::Secret") not in refs


def test_typed_receiver_disambiguates_method_call():
    """A method call on an annotated (Depends-injected) receiver resolves to THAT class's method
    even when other reachable modules define the same method name. Receiver-blind name+import
    gating drops it as ambiguous; the declared type `svc: FooService` disambiguates. Finding 2."""
    files = (
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
    calls = _pairs(resolve_structural(_reexport_nodes(*files)), "calls")
    assert ("routes.py::handler", "svc/foo.py::FooService.do_thing") in calls
    assert ("routes.py::handler", "svc/bar.py::BarService.do_thing") not in calls


def test_typed_receiver_resolves_inherited_method():
    """The receiver's type resolves the method up the inheritance chain (method defined on a base
    class in another module)."""
    files = (
        ("base.py", "class BaseSvc:\n    def do_thing(self, p):\n        return 1\n"),
        ("foo.py", "from base import BaseSvc\nclass FooService(BaseSvc):\n    pass\n"),
        (
            "routes.py",
            "from foo import FooService\n"
            "def handler(svc: FooService = Depends(FooService)):\n"
            "    return svc.do_thing(1)\n",
        ),
    )
    calls = _pairs(resolve_structural(_reexport_nodes(*files)), "calls")
    assert ("routes.py::handler", "base.py::BaseSvc.do_thing") in calls


def test_references_type_edge():
    # Impl.run has param ctx: Request — but Request isn't defined here, so no edge;
    # add a Request class and confirm the edge appears
    src = SRC_A + "\nclass Request: pass\n"
    assert ("m0.py::Impl.run", "m0.py::Request") in _pairs(
        _edges(src), "references_type"
    )


_BODY_CLASS_USE_CASES = [
    ("instantiation", "    w = Widget()\n"),
    ("attribute_access", "    _ = Widget.id\n"),
    ("call_argument", "    stmt = select(Widget)\n"),
    ("bare_name_argument", "    consume(Widget)\n"),
]


@pytest.mark.parametrize(
    "body",
    [c[1] for c in _BODY_CLASS_USE_CASES],
    ids=[c[0] for c in _BODY_CLASS_USE_CASES],
)
def test_body_class_use_creates_references_type_edge(body):
    """A class used as a *value* in a function body — instantiation, attribute access, or
    passed as a call argument — must edge to that class, even when the class never appears in
    the signature (the ORM `select(Model)` / `Model(**kw)` / `Model.col` case). Finding 3."""
    src = "class Widget:\n    id = 1\n\ndef uses() -> int:\n" + body + "    return 0\n"
    assert ("m0.py::uses", "m0.py::Widget") in _pairs(_edges(src), "references_type")


def _dedupe_first_wins(nodes):
    """Mirror build.py's repo-level dedup: the first node seen for an id wins, the rest are
    dropped. Same-id nodes must therefore already carry the union of every definition's facts."""
    seen, out = set(), []
    for n in nodes:
        if n.id not in seen:
            seen.add(n.id)
            out.append(n)
    return out


def test_same_named_method_edges_survive_build_dedup():
    """Full-pipeline guard for Finding A: `build` dedups same-id nodes first-wins, so the
    surviving node must already carry BOTH definitions' facts — else the `.expression` half's
    `references_type -> Model` is lost (total_out=0 for the `@hybrid_property`)."""
    src = (
        "class Model:\n    id = 1\n\n"
        "class Thing:\n"
        "    def label(self):\n"  # getter half — no interesting refs
        "        return 'x'\n"
        "    def label(cls):\n"  # .expression half — reads Model
        "        return select(Model.id)\n"
    )
    nodes = _dedupe_first_wins(extract_file_facts("m0.py", src, "production").nodes)
    refs = _pairs(resolve_structural(nodes), "references_type")
    assert ("m0.py::Thing.label", "m0.py::Model") in refs


def test_param_default_class_creates_references_type_edge():
    """Finding C end-to-end: a class used only as a parameter default edges to that class."""
    src = (
        "class Model:\n    id = 1\n\n"
        "def by_ids(ids, cls=Model) -> int:\n"
        "    return select(cls) or 0\n"
    )
    assert ("m0.py::by_ids", "m0.py::Model") in _pairs(_edges(src), "references_type")


def test_binding_disambiguates_name_defined_in_several_reachable_modules():
    """Finding B: `from a import Model` pins Model to module `a`, so the reference edge resolves
    even when another reachable module (`b`, imported for an unrelated symbol) also defines a
    same-named `Model`. Before, the two reachable `Model`s made `len(gated) > 1` and the edge was
    dropped as ambiguous — zeroing out the edges of a function whose refs are all common names."""
    files = (
        ("a.py", "class Model:\n    id = 1\n"),
        ("b.py", "class Model:\n    id = 2\n\ndef helper():\n    return 0\n"),
        (
            "caller.py",
            "from a import Model\n"
            "from b import helper\n"  # unrelated import that also drags in b.Model
            "def q() -> int:\n"
            "    helper()\n"
            "    return select(Model.id) or 0\n",
        ),
    )
    edges = resolve_structural(_reexport_nodes(*files))
    refs = _pairs(edges, "references_type")
    calls = _pairs(edges, "calls")
    assert ("caller.py::q", "a.py::Model") in refs  # binding pins Model to a
    assert ("caller.py::q", "b.py::Model") not in refs  # never the other module's Model
    assert ("caller.py::q", "b.py::helper") in calls  # unambiguous call still resolves


def test_reexport_binding_resolves_through_star_not_named_sibling():
    """Finding B, real case (orion `from orion.database import ComponentBlueprint`): a consumer
    imports a name a package re-exports, where the package star-re-exports the real class AND
    named-imports something else from a sibling module that *also* defines a same-named class.
    The named import drags that sibling into the consumer's reachability, so both same-named
    classes gate in (`len(gated) > 1`) and the edge is dropped as ambiguous. Resolving through the
    binding source's namespace — its own def + STAR re-exports only, NOT named imports — pins it to
    the one class the package actually exports."""
    files = (
        ("pkg/orm.py", "class Model:\n    id = 1\n"),  # the real (ORM) class
        (
            "pkg/other.py",
            "class Helper:\n    pass\n\nclass Model:\n    id = 2\n",  # same-named sibling
        ),
        (
            "pkg/__init__.py",
            "from pkg.orm import *\nfrom pkg.other import Helper\n",  # star Model, named Helper
        ),
        (
            "consumer.py",
            "from pkg import Model\ndef q() -> int:\n    return select(Model.id) or 0\n",
        ),
    )
    refs = _pairs(resolve_structural(_reexport_nodes(*files)), "references_type")
    assert ("consumer.py::q", "pkg/orm.py::Model") in refs  # the star-exported class
    assert (
        "consumer.py::q",
        "pkg/other.py::Model",
    ) not in refs  # never the named sibling's


def test_binding_resolves_through_named_init_reexport():
    """Finding B (moonbow `Model`): the ONLY definition is reachable from the consumer only via a
    NAMED re-export in a package `__init__` (`from .base import Model`), itself behind a star hop
    (`from pkg.models import *`). The star closure stops at the named hop, so the definer is
    unreachable → `len(gated) == 0` → drop. Following named `__init__` re-exports (a conventional
    re-export surface — but NOT a plain module's named import) recovers the edge."""
    files = (
        ("pkg/models/base.py", "class Model:\n    id = 1\n"),
        (
            "pkg/models/__init__.py",
            "from .base import Model\n",
        ),  # NAMED re-export through __init__
        ("pkg/__init__.py", "from pkg.models import *\n"),  # star hop
        (
            "consumer.py",
            "from pkg import Model\ndef q() -> int:\n    return select(Model.id) or 0\n",
        ),
    )
    refs = _pairs(resolve_structural(_reexport_nodes(*files)), "references_type")
    assert ("consumer.py::q", "pkg/models/base.py::Model") in refs


def test_body_class_ref_cross_module_gated_by_import():
    """Body class-refs resolve cross-module with the same import gate as annotations: only a
    class whose module the caller actually imports (and unambiguously) is edged."""
    caller = extract_file_facts(
        "caller.py",
        "from models import Widget\ndef uses() -> int:\n    Widget()\n    return 0\n",
        "production",
    )
    models = extract_file_facts("models.py", "class Widget:\n    pass\n", "production")
    other = extract_file_facts("other.py", "class Widget:\n    pass\n", "production")
    edges = resolve_structural([*caller.nodes, *models.nodes, *other.nodes])
    refs = {(e.src, e.dst) for e in edges if e.kind == "references_type"}
    assert ("caller.py::uses", "models.py::Widget") in refs  # imported → edge
    assert ("caller.py::uses", "other.py::Widget") not in refs  # not imported → no edge


def test_body_name_matching_function_does_not_become_references_type():
    """A body name that resolves to a *function* (not a class) stays a `calls` edge and does
    not spuriously become a references_type edge."""
    src = "def helper():\n    return 1\n\ndef uses():\n    return helper()\n"
    edges = _edges(src)
    assert ("m0.py::uses", "m0.py::helper") in _pairs(edges, "calls")
    assert ("m0.py::uses", "m0.py::helper") not in _pairs(edges, "references_type")


def test_contains_edges():
    assert ("m0.py::Impl", "m0.py::Impl.run") in _pairs(_edges(SRC_A), "contains")


def test_callback_arg_edge():
    # `caller` passes the bare name `run` as a positional arg to `helper(run)` ->
    # resolve_structural emits a callback_arg edge: caller -> run
    src = (
        "def helper(cb):\n    return cb()\n\n"
        "def run():\n    return 1\n\n"
        "def caller():\n    return helper(run)\n"
    )
    assert ("m0.py::caller", "m0.py::run") in _pairs(_edges(src), "callback_arg")


def test_imports_edges_resolve_within_repo():
    a = extract_file_facts(
        "pkg/a.py", "from pkg import b\nimport pkg.c\n", "production"
    )
    b = extract_file_facts("pkg/b.py", "x = 1\n", "production")
    c = extract_file_facts("pkg/c.py", "y = 2\n", "production")
    nodes = [*a.nodes, *b.nodes, *c.nodes]
    edges = resolve_structural(nodes)
    imports = {(e.src, e.dst) for e in edges if e.kind == "imports"}
    assert ("pkg/a.py", "pkg/b.py") in imports
    assert ("pkg/a.py", "pkg/c.py") in imports


def test_imports_edges_skip_unresolved_external():
    a = extract_file_facts(
        "pkg/a.py", "import numpy\nfrom requests import get\n", "production"
    )
    edges = resolve_structural(a.nodes)
    assert not [e for e in edges if e.kind == "imports"]  # external -> no edge


def test_registered_in_resolves_via_import_binding():
    app_mod = extract_file_facts("pkg/app.py", "app = object()\n", "production")
    routes = extract_file_facts(
        "pkg/routes.py",
        "from pkg.app import app\n\n@app.route('/x')\ndef handler():\n    pass\n",
        "production",
    )
    nodes = [*app_mod.nodes, *routes.nodes]
    edges = resolve_structural(nodes)
    reg = {(e.src, e.dst) for e in edges if e.kind == "registered_in"}
    assert ("pkg/routes.py::handler", "pkg/app.py") in reg


def test_registered_in_skips_external_registry():
    routes = extract_file_facts(
        "pkg/routes.py",
        "from flask import app\n\n@app.route('/x')\ndef handler():\n    pass\n",
        "production",
    )
    edges = resolve_structural(routes.nodes)
    assert not [e for e in edges if e.kind == "registered_in"]  # flask not in repo


def test_production_caller_does_not_resolve_to_test_def():
    prod = extract_file_facts(
        "svc.py",
        "def use():\n    return get()\n\ndef get():\n    return 1\n",
        "production",
    )
    # a test module ALSO defines get(); a production caller must NOT edge to it
    tst = extract_file_facts(
        "test_x.py",
        "def get():\n    return 2\n\ndef test_it():\n    return get()\n",
        "test",
    )
    nodes = [*prod.nodes, *tst.nodes]
    edges = resolve_structural(nodes)
    calls = {(e.src, e.dst) for e in edges if e.kind == "calls"}
    # production use() -> production get() only (svc.py is same module, so this already holds);
    # the key assertion: NO production -> test edge for the shared name
    assert ("svc.py::use", "test_x.py::get") not in calls
    # and the test caller MAY resolve to its own get()
    assert ("test_x.py::test_it", "test_x.py::get") in calls


def test_production_caller_cross_module_skips_test_targets():
    # production caller with NO same-module definition of the name -> must skip the test target
    prod = extract_file_facts(
        "svc.py",
        "from helper import handle\ndef use():\n    return handle()\n",
        "production",
    )
    helper = extract_file_facts(
        "helper.py", "def handle():\n    return 1\n", "production"
    )
    tst = extract_file_facts("test_x.py", "def handle():\n    return 2\n", "test")
    nodes = [*prod.nodes, *helper.nodes, *tst.nodes]
    edges = resolve_structural(nodes)
    calls = {(e.src, e.dst) for e in edges if e.kind == "calls"}
    assert ("svc.py::use", "helper.py::handle") in calls  # imported production target
    assert ("svc.py::use", "test_x.py::handle") not in calls  # never the test def


def test_module_contains_top_level_symbols():
    facts = extract_file_facts(
        "m.py",
        "def foo():\n    pass\n\nclass Bar:\n    def baz(self):\n        pass\n",
        "production",
    )
    edges = resolve_structural(facts.nodes)
    contains = {(e.src, e.dst) for e in edges if e.kind == "contains"}
    assert ("m.py", "m.py::foo") in contains  # module -> top-level function
    assert ("m.py", "m.py::Bar") in contains  # module -> top-level class
    assert (
        "m.py::Bar",
        "m.py::Bar.baz",
    ) in contains  # existing class -> method still holds
    assert (
        "m.py",
        "m.py::Bar.baz",
    ) not in contains  # module does NOT directly contain methods
