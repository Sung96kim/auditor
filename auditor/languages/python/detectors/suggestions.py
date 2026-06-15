"""Suggestion-tier OOP detectors — low-stakes nudges that render below the severity ladder
(``model_rebuild`` shortcuts, dict-mutation builders, module-consts that should be ClassVars,
thin-forwarding closures). Split out of ``oop.py`` to keep that module under the size budget.
"""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext
from auditor.languages.python.detectors._util import decorator_names
from auditor.languages.python.detectors.oop import _functions, _OopCandidate, _root_name
from auditor.models import Finding, Severity

#: Pydantic validator decorators whose contract *is* "receive the input mapping, mutate it, and
#: return it" — mutate-and-return there is the declared API, not a hidden one.
_VALIDATOR_DECORATORS = {
    "model_validator",
    "field_validator",
    "root_validator",
    "validator",
}


class _OopSuggestion(_OopCandidate):
    abstract: ClassVar[bool] = True
    default_severity: ClassVar[Severity] = Severity.SUGGESTION


class ModelRebuildShortcut(_OopSuggestion):
    rule_id: ClassVar[str] = "PY-OOP-MODEL-REBUILD"
    checklist_item: ClassVar[int] = 22

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "model_rebuild"
            ):
                target = _root_name(node.func.value) or "Model"
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"`{target}.model_rebuild()` — confirm a real circular import exists; otherwise import the referenced type directly",
                        suggestion="resolve the forward ref by importing the type, not a deferred rebuild",
                    )
                )
        return out


class DictMutationBuilder(_OopSuggestion):
    rule_id: ClassVar[str] = "PY-OOP-DICT-MUTATION-BUILDER"
    checklist_item: ClassVar[int] = 15

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for fn in _functions(ctx.tree):
            if decorator_names(fn) & _VALIDATOR_DECORATORS:
                continue
            params = {a.arg for a in fn.args.posonlyargs + fn.args.args}
            param = next((p for p in params if _mutates_and_returns(fn, p)), None)
            if param is not None:
                out.append(
                    self.make_finding(
                        ctx,
                        line=fn.lineno,
                        message=f"`{fn.name}` mutates dict param `{param}` in place and returns it; the contract is hidden",
                        suggestion="return a typed payload (or model_copy(update=...)) the caller merges, not a mutated dict",
                    )
                )
        return out


def _mutates_and_returns(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, param: str
) -> bool:
    mutates = any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Subscript)
            and isinstance(t.value, ast.Name)
            and t.value.id == param
            and isinstance(t.slice, ast.Constant)
            and isinstance(t.slice.value, str)
            for t in node.targets
        )
        for node in ast.walk(fn)
    )
    returns = any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Name)
        and node.value.id == param
        for node in ast.walk(fn)
    )
    return mutates and returns


class ModuleConstForSubclass(_OopSuggestion):
    rule_id: ClassVar[str] = "PY-OOP-MODULE-CONST-FOR-SUBCLASS"
    checklist_item: ClassVar[int] = 13

    def run(self, ctx: AuditContext) -> list[Finding]:
        min_consts = ctx.config.effective(self.rule_id).threshold.oop.module_const_min
        consts = _module_const_names(ctx.tree)
        out: list[Finding] = []
        for node in ctx.tree.body:
            if not (isinstance(node, ast.ClassDef) and _has_real_base(node)):
                continue
            prefix = _pascal_to_upper_snake(node.name)
            owned = [c for c in consts if c.lstrip("_").startswith(prefix + "_")]
            if len(owned) >= min_consts:
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"module constants ({', '.join(owned)}) hold data `{node.name}` should own; hoist to ClassVars",
                        suggestion="move the constants onto the subclass as ClassVars (e.g. KEY/TITLE/STEPS)",
                    )
                )
        return out


def _module_const_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    bare = t.id.lstrip("_")
                    if len(bare) > 1 and bare.isupper():
                        names.append(t.id)
    return names


def _has_real_base(cls: ast.ClassDef) -> bool:
    return any(not (isinstance(b, ast.Name) and b.id == "object") for b in cls.bases)


def _pascal_to_upper_snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.upper())
    return "".join(out)


class ClosureCapture(_OopSuggestion):
    rule_id: ClassVar[str] = "PY-OOP-CLOSURE-CAPTURE"
    checklist_item: ClassVar[int] = 18

    def run(self, ctx: AuditContext) -> list[Finding]:
        # recorder/capture closures (`cap`, `which`, …) are standard pytest scaffolding, not a smell
        if ctx.role.is_test:
            return []
        out: list[Finding] = []
        for outer in _functions(ctx.tree):
            outer_locals = _bound_names(outer)
            for inner in outer.body:
                if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                # A decorator's inner `*_wrapper` closing over the wrapped `fn` is the *defining*
                # shape of a decorator, not fragile capture — skip it.
                if _is_decorator_wrapper(inner):
                    continue
                # Only a thin forwarding closure (one `return <expr>`) is a clean partial/method
                # candidate; multi-statement work closures (a tx runner, a recursive accumulator)
                # legitimately close over their scope and are not the smell.
                if not (len(inner.body) == 1 and isinstance(inner.body[0], ast.Return)):
                    continue
                captured = _captured_from(inner, outer_locals)
                if captured and _used_as_value(outer, inner):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=inner.lineno,
                            message=f"`{inner.name}` captures `{', '.join(sorted(captured))}` from `{outer.name}` and is passed around; fragile and hard to test",
                            suggestion="bind the deps via functools.partial, or make it a method holding them on self",
                        )
                    )
        return out


def _is_decorator_wrapper(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """The inner function of a decorator: marked ``@functools.wraps`` or named ``*_wrapper``."""
    return (
        "wraps" in decorator_names(fn)
        or fn.name == "wrapper"
        or fn.name.endswith("_wrapper")
    )


def _bound_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    a = fn.args
    names = {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def _captured_from(
    inner: ast.FunctionDef | ast.AsyncFunctionDef, outer_locals: set[str]
) -> set[str]:
    inner_bound = _bound_names(inner)
    loaded = {
        node.id
        for node in ast.walk(inner)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return {name for name in loaded if name in outer_locals and name not in inner_bound}


def _used_as_value(
    outer: ast.FunctionDef | ast.AsyncFunctionDef,
    inner: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    inner_nodes = {id(n) for n in ast.walk(inner)}
    return any(
        isinstance(node, ast.Name)
        and node.id == inner.name
        and isinstance(node.ctx, ast.Load)
        and id(node) not in inner_nodes
        for node in ast.walk(outer)
    )
