import pytest

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


def test_nested_classes_qualified_by_outer():
    """Same-named nested classes must stay distinct nodes (qualified by their outer class),
    not collapse to one bare-name node."""
    src = (
        "class A:\n    class Args:\n        def go(self): ...\n"
        "class B:\n    class Args(Base):\n        pass\n"
    )
    ids = _by_id(extract_file_facts("m.py", src, "production"))
    assert "m.py::A.Args" in ids and "m.py::B.Args" in ids
    assert "m.py::Args" not in ids  # not collapsed to the bare name
    assert ids["m.py::A.Args"].name == "Args"  # simple name preserved (for resolution)
    assert "m.py::A.Args.go" in ids  # nested-class method qualified through the chain
    assert ids["m.py::B.Args"].bases == ("Base",)


def test_captures_attribute_and_subscript_bases():
    """inherits must capture attribute (`mod.Base`) and subscript (`Base[T]`) bases, not just
    bare Name bases."""
    src = (
        "class A(mod.Base):\n    pass\n"
        "class B(Generic[T]):\n    pass\n"
        "class C(pkg.Mix[int]):\n    pass\n"
    )
    ids = _by_id(extract_file_facts("m.py", src, "production"))
    assert ids["m.py::A"].bases == ("Base",)
    assert ids["m.py::B"].bases == ("Generic",)
    assert ids["m.py::C"].bases == ("Mix",)


def test_decorator_call_is_not_a_callee():
    """Regression: a decorator like @app.get(...) is applied TO the function, not called BY it,
    so its call must not show up in the function's callees (it created false `calls` edges, e.g.
    a `pong` healthcheck appearing to call SubmissionFieldsService.get)."""
    src = "@app.get('/ping')\nasync def pong() -> bool:\n    return True\n"
    pong = _by_id(extract_file_facts("m.py", src, "production"))["m.py::pong"]
    assert pong.callees == ()
    # a real body call IS still captured
    src2 = "@app.get('/x')\ndef h():\n    return do_work()\n"
    h = _by_id(extract_file_facts("m.py", src2, "production"))["m.py::h"]
    assert "do_work" in h.callees and "get" not in h.callees


def test_builtin_names_are_not_callees_or_callbacks():
    """Regression: builtin calls/args must not become edges. `dict(x)` / `x.dict()` aren't calls
    to a repo symbol named `dict`, and `isinstance(x, dict)` doesn't pass `dict` as a callback —
    these created false calls/callback_arg edges to a same-named repo class."""
    src = (
        "def f(x):\n"
        "    if isinstance(x, dict):\n"
        "        return dict(x)\n"
        "    return x.dict()\n"
    )
    f = _by_id(extract_file_facts("m.py", src, "production"))["m.py::f"]
    assert "dict" not in f.callees  # neither dict(x) nor x.dict()
    assert "dict" not in f.callback_names  # nor the isinstance() type arg
    assert "isinstance" not in f.callees  # builtin call itself isn't a callee


def test_body_class_refs_capture_loaded_names_excluding_stores_and_builtins():
    """class_refs holds body class-as-value candidates (Model(), Model.col, f(Model)) as loaded
    Names — assignment targets (Store) and builtins are excluded. The class-name gate at resolve
    time turns only the ones that are repo classes into references_type edges (Finding 3)."""
    src = "def f(x):\n    w = Widget()\n    _ = Other.col\n    return len(Thing)\n"
    f = _by_id(extract_file_facts("m.py", src, "production"))["m.py::f"]
    assert {"Widget", "Other", "Thing"} <= set(
        f.class_refs
    )  # loaded class-as-value names
    assert "w" not in f.class_refs  # Store assignment target excluded
    assert "len" not in f.class_refs  # builtin excluded


def test_same_named_methods_merge_into_one_node_unioning_facts():
    """Finding A: two methods sharing a name in a class (the `@hybrid_property` getter +
    `@<name>.expression`, or `@property` + `@<name>.setter`) share an id and must collapse to a
    SINGLE node whose facts are the UNION of both definitions — otherwise the later definition's
    references/calls (e.g. its `select(Model)`) are silently dropped at build-time dedup."""
    src = (
        "class Thing:\n"
        "    def label(self):\n"
        "        return Getter()\n"  # first def references Getter
        "    def label(cls):\n"
        "        return select(Expr.name)\n"  # second def references Expr
    )
    facts = extract_file_facts("m.py", src, "production")
    label = [n for n in facts.nodes if n.id == "m.py::Thing.label"]
    assert len(label) == 1  # collapsed to one node, not two
    assert {"Getter", "Expr"} <= set(label[0].class_refs)  # BOTH defs' refs survive


def test_param_default_reference_captured_in_class_refs():
    """Finding C: a class used only as a parameter default value (`def get(cls=Model)`) is a
    reference too — it must land in class_refs so it edges to the class, same as a body use."""
    src = "def by_ids(ids, cls=Model, *, ordering=Order):\n    return select(cls)\n"
    f = _by_id(extract_file_facts("m.py", src, "production"))["m.py::by_ids"]
    assert "Model" in f.class_refs  # positional-arg default
    assert "Order" in f.class_refs  # keyword-only default


def test_param_default_call_captured_in_callees():
    """A call inside a parameter default (`def f(x=make_default())`) is a real def-time
    dependency and must be captured as a callee, mirroring body call handling."""
    src = "def f(x=make_default()):\n    return x\n"
    f = _by_id(extract_file_facts("m.py", src, "production"))["m.py::f"]
    assert "make_default" in f.callees


def test_typed_calls_capture_receiver_type_and_method():
    """typed_calls pairs an annotated-receiver method call with the receiver's declared type
    (`svc: FooService` → svc.do_thing() gives ("FooService", "do_thing")) and self-calls with
    the enclosing class. Resolution uses these to disambiguate same-named methods (Finding 2)."""
    src = (
        "def handler(svc: FooService = Depends(FooService)):\n"
        "    return svc.do_thing(1)\n"
        "class A:\n"
        "    def f(self):\n"
        "        return self.g()\n"
        "    def g(self):\n"
        "        return 1\n"
    )
    ids = _by_id(extract_file_facts("m.py", src, "production"))
    assert ("FooService", "do_thing") in ids["m.py::handler"].typed_calls
    assert ("A", "g") in ids["m.py::A.f"].typed_calls


def test_param_types_cover_all_arg_kinds_including_keyword_only():
    """param_types must read annotations on EVERY parameter kind — positional-only, normal,
    *args, keyword-only (behind a `*,` separator), and **kwargs — not just positional. A method
    like `async def check(*, obj: Component | ComponentLink)` (ubiquitous in these query layers)
    otherwise records no type reference and drops its edge to the annotated classes (Finding B)."""
    src = "def f(a: A, /, b: B, *args: C, d: D, e: Component | Link, **kw: E) -> R:\n    pass\n"
    f = _by_id(extract_file_facts("m.py", src, "production"))["m.py::f"]
    assert {"A", "B", "C", "D", "Component", "Link", "E", "R"} <= set(f.param_types)


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


def test_extract_registry_roots():
    src = (
        "@app.route('/x')\n"
        "def handler():\n    pass\n\n"
        "@property\n"
        "def plain():\n    pass\n\n"
        "@registry.register\n"
        "class Thing:\n    pass\n"
    )
    facts = extract_file_facts("m.py", src, "production")
    handler = next(n for n in facts.nodes if n.name == "handler")
    plain = next(n for n in facts.nodes if n.name == "plain")
    thing = next(n for n in facts.nodes if n.name == "Thing")
    assert handler.registry_roots == ("app",)
    assert plain.registry_roots == ()  # bare-Name decorator is not a registry
    assert thing.registry_roots == ("registry",)


def test_extract_populates_semantic_profile():
    facts = extract_file_facts(
        "m.py", "def reader(x):\n    return db.get(x)\n", "production"
    )
    fn = next(n for n in facts.nodes if n.name == "reader")
    assert "returns_value" in fn.semantic_profile
    # module/class nodes carry no profile
    facts2 = extract_file_facts("c.py", "class C:\n    pass\n", "production")
    for n in facts2.nodes:
        if n.kind in ("module", "class"):
            assert n.semantic_profile == ()


def test_extract_module_imports_absolute_and_relative():
    src = (
        "import a.b.c\n"
        "from x.y import z\n"
        "from . import sib\n"
        "from .sub import thing as t\n"
    )
    facts = extract_file_facts("pkg/mod.py", src, "production")
    m = next(n for n in facts.nodes if n.kind == "module")
    # absolute
    assert "a.b.c" in m.imports
    assert "x.y" in m.imports and "x.y.z" in m.imports
    # relative: pkg/mod.py is in package "pkg"
    assert "pkg" in m.imports  # from . import sib  -> package itself
    assert "pkg.sib" in m.imports  # ... and the imported name as a possible submodule
    assert "pkg.sub" in m.imports and "pkg.sub.thing" in m.imports
    # bindings (local name -> source module)
    bindings = dict(m.import_bindings)
    assert bindings["z"] == "x.y"
    assert bindings["t"] == "pkg.sub"


_ATTR_CALL_SHAPES = [
    (
        "plain_receiver",
        "def f(obj):\n    return obj.method()\n",
        "m.py::f",
        ("obj", "method", True),
    ),
    (
        "self_receiver",
        "class A:\n    def f(self):\n        return self.method()\n",
        "m.py::A.f",
        ("self", "method", True),
    ),
    (
        "chained_receiver",
        "def f(a):\n    return a.b.method()\n",
        "m.py::f",
        ("a", "method", False),
    ),
    (
        "call_receiver",
        "def f():\n    return build().method()\n",
        "m.py::f",
        (None, "method", False),
    ),
    (
        "positional_default",
        "def f(x=cfg.load()):\n    return x\n",
        "m.py::f",
        ("cfg", "load", True),
    ),
    (
        "keyword_only_default",
        "def f(*, x=cfg.load()):\n    return x\n",
        "m.py::f",
        ("cfg", "load", True),
    ),
]


@pytest.mark.parametrize(
    ("src", "node_id", "expected"),
    [c[1:] for c in _ATTR_CALL_SHAPES],
    ids=[c[0] for c in _ATTR_CALL_SHAPES],
)
def test_attr_callees_record_receiver_root_method_and_directness(
    src, node_id, expected
):
    """attr_callees pairs each attribute call's method with the root name its receiver chains from
    and whether the receiver is that root itself, so the queue can tell `self.m()` from
    `self.dep.m()` from `re.search()`. `callees` keeps recording the bare method name."""
    fn = _by_id(extract_file_facts("m.py", src, "production"))[node_id]
    assert expected in fn.attr_callees
    assert expected[1] in fn.callees


def test_bare_call_records_a_bare_callee_only():
    """A bare-Name call has no receiver, so it lands in callees and bare_callees."""
    fn = _by_id(
        extract_file_facts("m.py", "def f():\n    return helper()\n", "production")
    )["m.py::f"]
    assert fn.callees == ("helper",)
    assert fn.bare_callees == ("helper",)
    assert fn.attr_callees == ()


def test_a_name_called_both_ways_records_both_forms():
    """`handle()` and `job.handle()` in one body: `callees` dedupes them to one name, so the two
    call forms have to be recorded apart for the queue to prefer the bare one."""
    fn = _by_id(
        extract_file_facts(
            "m.py", "def f(job):\n    return handle() or job.handle()\n", "production"
        )
    )["m.py::f"]
    assert fn.callees == ("handle",)
    assert fn.bare_callees == ("handle",)
    assert ("job", "handle", True) in fn.attr_callees


def test_builtin_attribute_call_is_not_an_attr_callee():
    """`x.dict()` is excluded from callees, so it must be excluded from attr_callees too."""
    fn = _by_id(
        extract_file_facts("m.py", "def f(x):\n    return x.dict()\n", "production")
    )["m.py::f"]
    assert fn.attr_callees == ()


def test_local_names_hold_the_parameters_and_the_assigned_names():
    """The bare-row gate reads these: a bare call or class reference naming one of them is naming
    the local, not a repo symbol."""
    src = (
        "def f(job):\n"
        "    handler = job\n"
        "    for item in job:\n"
        "        pass\n"
        "    return handler\n"
    )
    fn = _by_id(extract_file_facts("m.py", src, "production"))["m.py::f"]
    assert set(fn.local_names) == {"job", "handler", "item"}


def test_module_node_records_aliases_of_imported_callables():
    """`_RX = re.compile(...)` hides an imported object behind a module-level name; the pair is
    recorded so the resolver can mark calls on `_RX` externally bound."""
    src = (
        "import re\n"
        "from .util import build\n"
        "_RX = re.compile('x')\n"
        "CLIENT = build()\n"
        "PLAIN = 1\n"
    )
    mod = _by_id(extract_file_facts("pkg/m.py", src, "production"))["pkg/m.py"]
    assert ("_RX", "re") in mod.external_aliases
    assert ("CLIENT", "build") in mod.external_aliases
    assert [alias for alias, _ in mod.external_aliases] == ["_RX", "CLIENT"]


def test_fact_tuples_union_across_same_id_definitions():
    """Same-id nodes merge by unioning fact tuples, and the new call-form fields are in that set."""
    src = (
        "class Thing:\n"
        "    def label(self):\n"
        "        return self.first()\n"
        "    def label(cls):\n"
        "        return cls.second() or third()\n"
    )
    label = _by_id(extract_file_facts("m.py", src, "production"))["m.py::Thing.label"]
    assert ("self", "first", True) in label.attr_callees
    assert ("cls", "second", True) in label.attr_callees
    assert label.bare_callees == ("third",)
