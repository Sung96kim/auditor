"""Per-file AST extraction → FileGraphFacts. Stdlib only (runs in the core scan)."""

import ast

from auditor.graph.model import FileGraphFacts, GraphNode, NodeKind
from auditor.graph.tokens import normalize_tokens, split_ident, symbol_document

_FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)
_FuncDefT = ast.FunctionDef | ast.AsyncFunctionDef


def _module_dotted(rel_path: str) -> str:
    stem = rel_path.removesuffix(".py")
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


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


def extract_file_facts(rel_path: str, source: str, role: str) -> FileGraphFacts:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return FileGraphFacts(path=rel_path, role=role, nodes=[])

    path_tokens = normalize_tokens(
        split_ident(rel_path.removesuffix(".py").replace("/", " "))
    )
    nodes: list[GraphNode] = []

    module_doc = symbol_document(
        name=_module_dotted(rel_path).rsplit(".", 1)[-1],
        args=[],
        docstring=ast.get_docstring(tree) or "",
        body_idents=[],
        param_types=[],
        path_tokens=path_tokens,
        class_name=None,
    )
    nodes.append(
        GraphNode(
            id=rel_path,
            kind=NodeKind.MODULE,
            name=rel_path.rsplit("/", 1)[-1],
            module=rel_path,
            qualname=_module_dotted(rel_path),
            doc_tokens=tuple(module_doc),
            line=1,
            role=role,
        )
    )

    def fn_node(fn: _FuncDefT, cls: str | None) -> GraphNode:
        params = [a.arg for a in fn.args.posonlyargs + fn.args.args]
        callees: list[str] = []
        callback_names: list[str] = []
        body_idents: list[str] = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Name):
                body_idents.append(n.id)
            elif isinstance(n, ast.Attribute):
                body_idents.append(n.attr)
            elif isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    callees.append(f.id)
                elif isinstance(f, ast.Attribute):
                    callees.append(f.attr)
                for a in (
                    n.args
                ):  # bare Name passed as a positional arg (potential callback)
                    if isinstance(a, ast.Name):
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
            path_tokens=path_tokens,
            class_name=cls,
        )
        return GraphNode(
            id=f"{rel_path}::{qual}",
            kind=NodeKind.METHOD if cls else NodeKind.FUNCTION,
            name=fn.name,
            module=rel_path,
            qualname=qual,
            doc_tokens=tuple(doc),
            callees=tuple(dict.fromkeys(callees)),
            param_types=tuple(dict.fromkeys(ptypes)),
            decorators=decorators,
            callback_names=tuple(dict.fromkeys(callback_names)),
            is_hof=is_hof,
            is_stub=_is_stub(fn),
            line=fn.lineno,
            role=role,
        )

    def walk(node: ast.AST, cls: str | None) -> None:
        for child in getattr(node, "body", []):
            if isinstance(child, ast.ClassDef):
                methods = [s.name for s in child.body if isinstance(s, _FuncDef)]
                doc = symbol_document(
                    name=child.name,
                    args=[],
                    docstring=ast.get_docstring(child) or "",
                    body_idents=methods,
                    param_types=[],
                    path_tokens=path_tokens,
                    class_name=None,
                )
                nodes.append(
                    GraphNode(
                        id=f"{rel_path}::{child.name}",
                        kind=NodeKind.CLASS,
                        name=child.name,
                        module=rel_path,
                        qualname=child.name,
                        doc_tokens=tuple(doc),
                        bases=tuple(
                            b.id for b in child.bases if isinstance(b, ast.Name)
                        ),
                        method_names=tuple(methods),
                        line=child.lineno,
                        role=role,
                    )
                )
                walk(child, child.name)
            elif isinstance(child, _FuncDef):
                nodes.append(fn_node(child, cls))

    walk(tree, None)
    return FileGraphFacts(path=rel_path, role=role, nodes=nodes)
