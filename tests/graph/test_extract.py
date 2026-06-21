from auditor.graph.extract import extract_file_facts

SRC = '''
class Base:
    def run(self): ...

class Impl(Base):
    def run(self, ctx: Request) -> Response:
        """Handle the request."""
        return self.process(ctx)

def helper(cb):
    return cb()

def caller():
    return helper(run)
'''


def _by_id(facts):
    return {n.id: n for n in facts.nodes}


def test_extracts_classes_methods_functions():
    facts = extract_file_facts("m.py", SRC, "production")
    ids = _by_id(facts)
    assert "m.py::Base" in ids and ids["m.py::Base"].kind == "class"
    assert "m.py::Impl.run" in ids and ids["m.py::Impl.run"].kind == "method"
    assert "m.py::helper" in ids and ids["m.py::helper"].kind == "function"


def test_class_records_bases_and_methods():
    impl = _by_id(extract_file_facts("m.py", SRC, "production"))["m.py::Impl"]
    assert impl.bases == ("Base",) and "run" in impl.method_names


def test_method_captures_param_types_callees_and_doc():
    run = _by_id(extract_file_facts("m.py", SRC, "production"))["m.py::Impl.run"]
    assert "Request" in run.param_types and "Response" in run.param_types
    assert "process" in run.callees and "request" in run.doc_tokens


def test_stub_and_hof_and_callback_flags():
    ids = _by_id(extract_file_facts("m.py", SRC, "production"))
    assert ids["m.py::Base.run"].is_stub is True  # `...` body
    assert ids["m.py::helper"].is_hof is True  # calls its param `cb`
    assert "run" in ids["m.py::caller"].callback_names  # passes `run` as an arg
    assert (
        ids["m.py::caller"].is_hof is False
    )  # passes a free name, but has no params → not a HOF


def test_syntax_error_returns_empty():
    assert extract_file_facts("bad.py", "def (:", "production").nodes == []


def test_extract_emits_module_node():
    facts = extract_file_facts("pkg/sub/mod.py", "def foo():\n    pass\n", "production")
    mods = [n for n in facts.nodes if n.kind == "module"]
    assert len(mods) == 1
    m = mods[0]
    assert m.id == "pkg/sub/mod.py"
    assert m.qualname == "pkg.sub.mod"
    assert m.module == "pkg/sub/mod.py"
    assert m.role == "production"


def test_extract_module_node_for_init_drops_init_segment():
    facts = extract_file_facts("pkg/__init__.py", "x = 1\n", "production")
    m = next(n for n in facts.nodes if n.kind == "module")
    assert m.qualname == "pkg"
