from auditor.graph.extract import extract_file_facts
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


def _edges(*sources):
    nodes = []
    for i, s in enumerate(sources):
        nodes += extract_file_facts(f"m{i}.py", s, "production").nodes
    return resolve_structural(nodes)


def _pairs(edges, kind):
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
