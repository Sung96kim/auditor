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


def test_references_type_edge():
    # Impl.run has param ctx: Request — but Request isn't defined here, so no edge;
    # add a Request class and confirm the edge appears
    src = SRC_A + "\nclass Request: pass\n"
    assert ("m0.py::Impl.run", "m0.py::Request") in _pairs(
        _edges(src), "references_type"
    )


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
