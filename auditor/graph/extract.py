"""Per-file AST extraction → FileGraphFacts. Stdlib only (runs in the core scan)."""

import ast
import builtins

from auditor.graph import semantic_profile
from auditor.graph.model import FileGraphFacts, GraphNode, NodeKind
from auditor.graph.tokens import normalize_tokens, split_ident, symbol_document

_FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)
_FuncDefT = ast.FunctionDef | ast.AsyncFunctionDef
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


def _is_stub(fn: _FuncDefT) -> bool:
    body = [
        s
        for s in fn.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    if not body:
        return True
    return len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Raise))


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
        return FileGraphFacts(path=self.rel_path, role=self.role, nodes=self.nodes)

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
        params = [a.arg for a in fn.args.posonlyargs + fn.args.args]
        callees: list[str] = []
        callback_names: list[str] = []
        body_idents: list[str] = []
        # walk the body statements only — NOT fn.decorator_list: a decorator like
        # @app.get("/ping") is applied TO the function, not called BY it, so it must not
        # become one of its callees (param/return types are collected separately below).
        for stmt in fn.body:
            for n in ast.walk(stmt):
                if isinstance(n, ast.Name):
                    body_idents.append(n.id)
                elif isinstance(n, ast.Attribute):
                    body_idents.append(n.attr)
                elif isinstance(n, ast.Call):
                    f = n.func
                    if isinstance(f, ast.Name):
                        if f.id not in _BUILTIN_NAMES:
                            callees.append(f.id)
                    elif isinstance(f, ast.Attribute):
                        if f.attr not in _BUILTIN_NAMES:
                            callees.append(f.attr)
                    for a in n.args:  # bare Name positional arg (potential callback)
                        if isinstance(a, ast.Name) and a.id not in _BUILTIN_NAMES:
                            callback_names.append(a.id)
        ptypes: list[str] = []
        for a in fn.args.posonlyargs + fn.args.args:
            ptypes += _ann_type_names(a.annotation)
        ptypes += _ann_type_names(fn.returns)
        # HOF only on a bare-Name call of a parameter (spec §9c: avoid the 53% over-fire)
        is_hof = any(c in params for c in callees) or any(
            n in params for n in callback_names
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
            body_idents=body_idents,
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
            callees=tuple(dict.fromkeys(callees)),
            param_types=tuple(dict.fromkeys(ptypes)),
            decorators=decorators,
            registry_roots=_registry_roots(fn.decorator_list),
            semantic_profile=semantic_profile.compute(fn),
            callback_names=tuple(dict.fromkeys(callback_names)),
            is_hof=is_hof,
            is_stub=_is_stub(fn),
            line=fn.lineno,
            role=self.role,
        )

    def _walk(self, node: ast.AST, cls: str | None) -> None:
        for child in getattr(node, "body", []):
            if isinstance(child, ast.ClassDef):
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
                        id=f"{self.rel_path}::{child.name}",
                        kind=NodeKind.CLASS,
                        name=child.name,
                        module=self.rel_path,
                        qualname=child.name,
                        doc_tokens=tuple(doc),
                        bases=tuple(
                            b.id for b in child.bases if isinstance(b, ast.Name)
                        ),
                        method_names=tuple(methods),
                        registry_roots=_registry_roots(child.decorator_list),
                        line=child.lineno,
                        role=self.role,
                    )
                )
                self._walk(child, child.name)
            elif isinstance(child, _FuncDef):
                self.nodes.append(self._fn_node(child, cls))


def extract_file_facts(rel_path: str, source: str, role: str) -> FileGraphFacts:
    return FileExtractor(rel_path, source, role).extract()
