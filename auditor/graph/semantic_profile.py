"""Single source for the Høst-Østvold behavior attributes of a function/method.
`ATTRS` is the canonical ordered tuple (the ONLY place it is defined); `compute`
returns the subset that holds for one AST node. Extraction stores the result on the
node; the naming-inconsistency detector aggregates it per verb."""

import ast

ATTRS = (
    "returns_value",
    "returns_none",
    "returns_bool",
    "no_params",
    "reads_self",
    "writes_self",
    "creates_obj",
    "has_loop",
    "has_branch",
    "multi_return",
    "raises",
    "catches",
    "recursive",
    "is_async",
    "awaits",
    "comprehension",
)

_FuncDefT = ast.FunctionDef | ast.AsyncFunctionDef


def compute(fn: _FuncDefT) -> tuple[str, ...]:
    attrs: set[str] = set()
    if isinstance(fn, ast.AsyncFunctionDef):
        attrs.add("is_async")
    args = [a.arg for a in fn.args.posonlyargs + fn.args.args if a.arg != "self"]
    if not args:
        attrs.add("no_params")
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    if len(returns) > 1:
        attrs.add("multi_return")
    for r in returns:
        if r.value is not None and not (
            isinstance(r.value, ast.Constant) and r.value.value is None
        ):
            attrs.add("returns_value")
            if isinstance(r.value, ast.Compare) or (
                isinstance(r.value, ast.Constant) and isinstance(r.value.value, bool)
            ):
                attrs.add("returns_bool")
    if "returns_value" not in attrs:
        attrs.add("returns_none")
    for n in ast.walk(fn):
        if isinstance(n, (ast.For, ast.While, ast.AsyncFor)):
            attrs.add("has_loop")
        elif isinstance(n, ast.If):
            attrs.add("has_branch")
        elif isinstance(n, ast.Raise):
            attrs.add("raises")
        elif isinstance(n, ast.ExceptHandler):
            attrs.add("catches")
        elif isinstance(n, ast.Await):
            attrs.add("awaits")
        elif isinstance(n, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
            attrs.add("comprehension")
        elif (
            isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == "self"
        ):
            attrs.add("writes_self" if isinstance(n.ctx, ast.Store) else "reads_self")
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            if n.func.id[:1].isupper():
                attrs.add("creates_obj")
            if n.func.id == fn.name:
                attrs.add("recursive")
    return tuple(sorted(attrs))
