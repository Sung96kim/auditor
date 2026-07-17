"""Per-file AST extraction → FileGraphFacts. Stdlib only (runs in the core scan)."""

import ast
import builtins

from auditor.graph import semantic_profile
from auditor.graph.model import FileGraphFacts, GraphNode, NodeKind
from auditor.graph.tokens import normalize_tokens, split_ident, symbol_document

_FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)
_FuncDefT = ast.FunctionDef | ast.AsyncFunctionDef


def _base_name(b: ast.expr) -> str | None:
    """Best-effort base-class name from a class base expr: ``Base`` (Name), ``mod.Base``
    (Attribute), or ``Base[T]`` (Subscript, e.g. Generic/pydantic) all yield ``Base``."""
    if isinstance(b, ast.Name):
        return b.id
    if isinstance(b, ast.Attribute):
        return b.attr
    if isinstance(b, ast.Subscript):
        return _base_name(b.value)
    return None


# Python's own builtin names (dict, list, str, isinstance, type, …) — not a hand-curated list.
# `dict(x)`/`x.dict()` and `isinstance(x, dict)` must not become call/callback edges to a repo
# symbol that happens to share a builtin's name.
_BUILTIN_NAMES = frozenset(dir(builtins))


def _module_dotted(rel_path: str) -> str:
    stem = rel_path.removesuffix(".py")
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def _module_imports(
    rel_path: str, tree: ast.Module
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    pkg_parts = rel_path.split("/")[:-1]  # directory parts of the importing file
    targets: list[str] = []
    bindings: list[tuple[str, str]] = []
    for node in tree.body:  # module level only
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
                local = alias.asname or alias.name.split(".")[0]
                bindings.append((local, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative
                base_parts = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                base = ".".join(base_parts)
                source = f"{base}.{node.module}" if node.module else base
            else:
                source = node.module or ""
            if not source:
                continue
            targets.append(source)
            for alias in node.names:
                targets.append(f"{source}.{alias.name}")
                local = alias.asname or alias.name
                bindings.append((local, source))
    # dedupe, preserve first-seen order
    return tuple(dict.fromkeys(targets)), tuple(dict.fromkeys(bindings))


def _registry_roots(decorator_list: list[ast.expr]) -> tuple[str, ...]:
    roots: list[str] = []
    for d in decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if not isinstance(target, ast.Attribute):
            continue  # bare-Name decorators (@property, @dataclass) are not registries
        cur: ast.expr = target.value
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name):
            roots.append(cur.id)
    return tuple(dict.fromkeys(roots))


def _ann_type_names(node: ast.expr | None) -> list[str]:
    if node is None:
        return []
    names = [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]
    names += [n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)]
    return names


def _all_arg_nodes(args: ast.arguments) -> list[ast.arg]:
    """Every declared parameter — positional-only, normal, ``*args``, keyword-only, ``**kwargs`` —
    so an annotation on a keyword-only param (behind a ``*,``) is still read (Finding B)."""
    extra = [a for a in (args.vararg, args.kwarg) if a is not None]
    return args.posonlyargs + args.args + args.kwonlyargs + extra


def _receiver_type(ann: ast.expr | None) -> str | None:
    """The class short-name a receiver annotation denotes, for typed method-call resolution:
    ``FooService`` (Name), ``mod.FooService`` (Attribute), or the primary side of an
    ``X | None`` union. ``None`` for containers/forward-refs/anything else (kept deliberately
    narrow — the common ``svc: FooService`` / ``svc: FooService | None`` shapes only)."""
    if isinstance(ann, ast.Name):
        return ann.id
    if isinstance(ann, ast.Attribute):
        return ann.attr
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return _receiver_type(ann.left) or _receiver_type(ann.right)
    return None


def _is_stub(fn: _FuncDefT) -> bool:
    body = [
        s
        for s in fn.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    if not body:
        return True
    return len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Raise))


# Fact tuples unioned on a same-id merge; identity scalars kept from the first def (Finding A).
_UNION_FACT_FIELDS = (
    "doc_tokens",
    "callees",
    "param_types",
    "decorators",
    "bases",
    "method_names",
    "callback_names",
    "class_refs",
    "typed_calls",
    "imports",
    "import_bindings",
    "registry_roots",
    "semantic_profile",
)


def _union_facts(a: GraphNode, b: GraphNode) -> GraphNode:
    """Merge two same-id nodes, unioning their fact tuples — so same-named methods
    (`@hybrid_property` getter + `.expression`) don't lose the later def's edges (Finding A)."""
    update: dict[str, object] = {
        f: tuple(dict.fromkeys((*getattr(a, f), *getattr(b, f))))
        for f in _UNION_FACT_FIELDS
    }
    update["is_hof"] = a.is_hof or b.is_hof
    update["is_stub"] = a.is_stub and b.is_stub
    return a.model_copy(update=update)


def _merge_same_id(nodes: list[GraphNode]) -> list[GraphNode]:
    """Collapse same-id nodes to one merged node, preserving first-seen order and identity."""
    by_id: dict[str, GraphNode] = {}
    for n in nodes:
        by_id[n.id] = _union_facts(by_id[n.id], n) if n.id in by_id else n
    return list(by_id.values())


class _FnFactCollector:
    """A function's referenced symbols, accumulated from its body and parameter defaults."""

    def __init__(self, cls: str | None) -> None:
        self.callees: list[str] = []
        self.callback_names: list[str] = []
        self.body_idents: list[str] = []
        self.class_refs: list[
            str
        ] = []  # loaded Names — class-as-value uses (Model(), Model.col)
        self.attr_calls: list[
            tuple[str, str]
        ] = []  # (receiver, method) for recv.method()
        self.recv_types: dict[
            str, str
        ] = {}  # receiver var -> declared class, for typed calls
        if cls is not None:
            self.recv_types["self"] = self.recv_types["cls"] = cls.rsplit(".", 1)[-1]

    def scan(self, fn: _FuncDefT) -> "_FnFactCollector":
        for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs:
            if (t := _receiver_type(a.annotation)) is not None:
                self.recv_types[a.arg] = t
        self._scan_body(fn.body)
        self._scan_defaults(fn.args)
        return self

    def _add_call(self, call: ast.Call, *, track_receiver: bool) -> None:
        f = call.func
        if isinstance(f, ast.Name) and f.id not in _BUILTIN_NAMES:
            self.callees.append(f.id)
        elif isinstance(f, ast.Attribute) and f.attr not in _BUILTIN_NAMES:
            self.callees.append(f.attr)
            if track_receiver and isinstance(f.value, ast.Name):
                self.attr_calls.append((f.value.id, f.attr))

    def _scan_body(self, body: list[ast.stmt]) -> None:
        # walk the body only — a decorator (`@app.get(...)`) is applied TO the fn, not called BY it
        for stmt in body:
            for n in ast.walk(stmt):
                if isinstance(n, ast.Name):
                    self.body_idents.append(n.id)
                    if isinstance(n.ctx, ast.Load) and n.id not in _BUILTIN_NAMES:
                        self.class_refs.append(n.id)
                elif isinstance(n, ast.Attribute):
                    self.body_idents.append(n.attr)
                elif isinstance(n, ast.AnnAssign):
                    if (
                        isinstance(n.target, ast.Name)
                        and (t := _receiver_type(n.annotation)) is not None
                    ):
                        self.recv_types[n.target.id] = t
                elif isinstance(n, ast.Call):
                    self._add_call(n, track_receiver=True)
                    for a in n.args:  # bare Name positional arg (potential callback)
                        if isinstance(a, ast.Name) and a.id not in _BUILTIN_NAMES:
                            self.callback_names.append(a.id)

    def _scan_defaults(self, args: ast.arguments) -> None:
        # a class/callable used only as a param default is still a reference (Finding C)
        for d in (*args.defaults, *args.kw_defaults):
            if d is None:  # kwonly arg with no default
                continue
            for n in ast.walk(d):
                if (
                    isinstance(n, ast.Name)
                    and isinstance(n.ctx, ast.Load)
                    and n.id not in _BUILTIN_NAMES
                ):
                    self.class_refs.append(n.id)
                elif isinstance(n, ast.Call):
                    self._add_call(n, track_receiver=False)

    def typed_calls(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            dict.fromkeys(
                (self.recv_types[r], m)
                for r, m in dict.fromkeys(self.attr_calls)
                if r in self.recv_types
            )
        )


class FileExtractor:
    """Extracts one file's FileGraphFacts. Holds the per-file context (path, role,
    path tokens) and accumulates nodes, so the AST walk reads as methods, not closures."""

    def __init__(self, rel_path: str, source: str, role: str) -> None:
        self.rel_path = rel_path
        self.source = source
        self.role = role
        self.path_tokens = normalize_tokens(
            split_ident(rel_path.removesuffix(".py").replace("/", " "))
        )
        self.nodes: list[GraphNode] = []

    def extract(self) -> FileGraphFacts:
        try:
            tree = ast.parse(self.source)
        except (SyntaxError, ValueError):
            return FileGraphFacts(path=self.rel_path, role=self.role, nodes=[])
        self._module_node(tree)
        self._walk(tree, None)
        return FileGraphFacts(
            path=self.rel_path, role=self.role, nodes=_merge_same_id(self.nodes)
        )

    def _module_node(self, tree: ast.Module) -> None:
        imports, import_bindings = _module_imports(self.rel_path, tree)
        module_doc = symbol_document(
            name=_module_dotted(self.rel_path).rsplit(".", 1)[-1],
            args=[],
            docstring=ast.get_docstring(tree) or "",
            body_idents=[],
            param_types=[],
            path_tokens=self.path_tokens,
            class_name=None,
        )
        self.nodes.append(
            GraphNode(
                id=self.rel_path,
                kind=NodeKind.MODULE,
                name=self.rel_path.rsplit("/", 1)[-1],
                module=self.rel_path,
                qualname=_module_dotted(self.rel_path),
                doc_tokens=tuple(module_doc),
                imports=imports,
                import_bindings=import_bindings,
                line=1,
                role=self.role,
            )
        )

    def _fn_node(self, fn: _FuncDefT, cls: str | None) -> GraphNode:
        facts = _FnFactCollector(cls).scan(fn)
        params = [a.arg for a in fn.args.posonlyargs + fn.args.args]
        ptypes: list[str] = []
        for a in _all_arg_nodes(fn.args):
            ptypes += _ann_type_names(a.annotation)
        ptypes += _ann_type_names(fn.returns)
        # HOF only on a bare-Name call of a parameter (spec §9c: avoid the 53% over-fire)
        is_hof = any(c in params for c in facts.callees) or any(
            n in params for n in facts.callback_names
        )
        decorators = tuple(
            d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")
            for d in fn.decorator_list
        )
        qual = fn.name if cls is None else f"{cls}.{fn.name}"
        doc = symbol_document(
            name=fn.name,
            args=params,
            docstring=ast.get_docstring(fn) or "",
            body_idents=facts.body_idents,
            param_types=ptypes,
            path_tokens=self.path_tokens,
            class_name=cls,
        )
        return GraphNode(
            id=f"{self.rel_path}::{qual}",
            kind=NodeKind.METHOD if cls else NodeKind.FUNCTION,
            name=fn.name,
            module=self.rel_path,
            qualname=qual,
            doc_tokens=tuple(doc),
            callees=tuple(dict.fromkeys(facts.callees)),
            param_types=tuple(dict.fromkeys(ptypes)),
            decorators=decorators,
            registry_roots=_registry_roots(fn.decorator_list),
            semantic_profile=semantic_profile.compute(fn),
            callback_names=tuple(dict.fromkeys(facts.callback_names)),
            class_refs=tuple(dict.fromkeys(facts.class_refs)),
            typed_calls=facts.typed_calls(),
            is_hof=is_hof,
            is_stub=_is_stub(fn),
            line=fn.lineno,
            role=self.role,
        )

    def _walk(self, node: ast.AST, cls: str | None) -> None:
        for child in getattr(node, "body", []):
            if isinstance(child, ast.ClassDef):
                # qualify nested classes by their outer class so same-named nested classes
                # (e.g. many `class Arguments(...)` under different parents) stay distinct nodes
                qual = child.name if cls is None else f"{cls}.{child.name}"
                methods = [s.name for s in child.body if isinstance(s, _FuncDef)]
                doc = symbol_document(
                    name=child.name,
                    args=[],
                    docstring=ast.get_docstring(child) or "",
                    body_idents=methods,
                    param_types=[],
                    path_tokens=self.path_tokens,
                    class_name=None,
                )
                self.nodes.append(
                    GraphNode(
                        id=f"{self.rel_path}::{qual}",
                        kind=NodeKind.CLASS,
                        name=child.name,
                        module=self.rel_path,
                        qualname=qual,
                        doc_tokens=tuple(doc),
                        bases=tuple(
                            n for b in child.bases if (n := _base_name(b)) is not None
                        ),
                        method_names=tuple(methods),
                        registry_roots=_registry_roots(child.decorator_list),
                        line=child.lineno,
                        role=self.role,
                    )
                )
                self._walk(child, qual)
            elif isinstance(child, _FuncDef):
                self.nodes.append(self._fn_node(child, cls))


def extract_file_facts(rel_path: str, source: str, role: str) -> FileGraphFacts:
    return FileExtractor(rel_path, source, role).extract()
