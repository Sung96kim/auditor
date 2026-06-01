"""OOP/composition-category detectors: constructor walls, flat-field models, thin
wrappers, builder classes, dispatch ladders, static-method classes, free-function
orchestrators, long parameter lists, god classes, high complexity, dataclass-in-pydantic.

Mostly ``candidate`` (the agent judges); ``PY-OOP-DATACLASS-IN-PYDANTIC`` is auto.
"""

import ast
from collections import defaultdict
from collections.abc import Iterator
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import dotted_name
from auditor.models import Category, Finding, Severity, VerdictKind

_MIN_TWIN_TOKENS = 4  # a parallel-sibling fingerprint needs substance, else trivial fns collide
_TWIN_STRUCT = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Return,
    ast.Try,
    ast.With,
    ast.Raise,
    ast.IfExp,
)


class _OopCandidate(Detector):
    abstract: ClassVar[bool] = True
    category: ClassVar[Category] = Category.OOP_COMPOSITION
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    default_severity: ClassVar[Severity] = Severity.LOW


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _method_lines(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.add(sub.lineno)
    return out


class DataclassInPydantic(Detector):
    rule_id: ClassVar[str] = "PY-OOP-DATACLASS-IN-PYDANTIC"
    category: ClassVar[Category] = Category.OOP_COMPOSITION
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    checklist_item: ClassVar[int] = 5

    def run(self, ctx: AuditContext) -> list[Finding]:
        if "pydantic" not in ctx.project_deps:
            return []
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.ClassDef):
                decs = {dotted_name(d).split(".")[-1] for d in node.decorator_list}
                if "dataclass" in decs:
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message=f"@dataclass `{node.name}` in a Pydantic project; use BaseModel",
                            suggestion="migrate to pydantic.BaseModel (ConfigDict(frozen=True) if frozen)",
                        )
                    )
        return out


class ConstructorWall(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-CONSTRUCTOR-WALL"
    checklist_item: ClassVar[int] = 3

    def run(self, ctx: AuditContext) -> list[Finding]:
        threshold = ctx.config.effective(self.rule_id).threshold.wall_kwarg_min
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call) and len(node.keywords) >= threshold:
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                )
                if name and name[:1].isupper():
                    kwargs = [kw.arg for kw in node.keywords if kw.arg]
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message=f"`{name}(...)` constructor wall: {len(node.keywords)} kwargs",
                            evidence=", ".join(kwargs),
                            suggestion="group cohesive fields into composed sub-models",
                        )
                    )
        return out


class FlatFieldModel(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-FLAT-FIELD-MODEL"
    checklist_item: ClassVar[int] = 4

    def run(self, ctx: AuditContext) -> list[Finding]:
        threshold = ctx.config.effective(self.rule_id).threshold.flat_field_min
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {dotted_name(b).split(".")[-1] for b in node.bases}
            if "BaseModel" not in base_names:
                continue
            fields = [
                s.target.id
                for s in node.body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            ]
            if len(fields) >= threshold:
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"flat model `{node.name}` has {len(fields)} fields; compose sub-models",
                        evidence=", ".join(fields),
                        suggestion="group cohesive fields into nested sub-models",
                    )
                )
        return out


class ThinWrapper(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-THIN-WRAPPER"
    checklist_item: ClassVar[int] = 8

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.iter_child_nodes(ctx.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = [s for s in node.body if not _is_docstring(s)]
            if (
                len(body) == 1
                and isinstance(body[0], ast.Return)
                and _is_pure_forward(node, body[0].value)
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"thin wrapper `{node.name}` forwards its args verbatim; call the underlying directly",
                        suggestion="delete the wrapper, or keep only if the name adds real meaning",
                    )
                )
        return out


def _is_pure_forward(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, value: ast.expr | None
) -> bool:
    """A do-nothing pass-through: ``def f(a, b): return g(a, b)`` — forwards exactly the
    positional params, in order, to one call (no extra/keyword/star args)."""
    if not isinstance(value, ast.Call) or value.keywords:
        return False
    if any(isinstance(a, ast.Starred) for a in value.args):
        return False
    params = [p.arg for p in fn.args.posonlyargs + fn.args.args]
    forwarded = [a.id for a in value.args if isinstance(a, ast.Name)]
    return len(forwarded) == len(value.args) and forwarded == params and bool(params)


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


_FACTORY_VERBS = {"build", "create", "make", "construct", "produce"}


class BuilderClass(_OopCandidate):
    """A class that holds inputs and exposes one public produce-method (build/create/…) is a
    function-with-state — better as a factory classmethod ``Result.from_X(...)``."""

    rule_id: ClassVar[str] = "PY-OOP-BUILDER-CLASS"
    checklist_item: ClassVar[int] = 9

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = [
                s
                for s in node.body
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            public = [m.name for m in methods if not m.name.startswith("_")]
            has_init = any(m.name == "__init__" for m in methods)
            named_builder = node.name.endswith("Builder")
            # one public "produce" method, and the class either holds state or is *Builder
            if (
                len(public) == 1
                and public[0] in _FACTORY_VERBS
                and (has_init or named_builder)
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"`{node.name}` holds inputs and produces one output via `.{public[0]}()` — potential factory refactor",
                        suggestion="replace with a factory classmethod on the result (e.g. Result.from_X(...))",
                    )
                )
        return out


class DispatchLadder(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-DISPATCH-LADDER"
    checklist_item: ClassVar[int] = 12

    def run(self, ctx: AuditContext) -> list[Finding]:
        threshold = ctx.config.effective(self.rule_id).threshold.dispatch_min_branches
        out: list[Finding] = []
        for fn in _functions(ctx.tree):
            for stmt in ast.walk(fn):
                if isinstance(stmt, ast.If):
                    branches = _elif_chain_len(stmt)
                    if branches >= threshold:
                        out.append(
                            self.make_finding(
                                ctx,
                                line=stmt.lineno,
                                message=f"if/elif dispatch ladder ({branches} branches)",
                                suggestion="replace the ladder with a registered subclass family",
                            )
                        )
                        break
        return out


def _elif_chain_len(node: ast.If) -> int:
    count = 1
    cur = node
    while len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
        count += 1
        cur = cur.orelse[0]
    return count


class StaticMethodClass(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-STATIC-METHOD-CLASS"
    checklist_item: ClassVar[int] = 14

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = [
                s
                for s in node.body
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if methods and all(
                any(
                    dotted_name(d).split(".")[-1] == "staticmethod"
                    for d in m.decorator_list
                )
                for m in methods
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"`{node.name}` is all @staticmethod; flatten to functions or real OOP",
                        suggestion="module-level functions, or genuine instance state",
                    )
                )
        return out


class LongParamList(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-LONG-PARAM-LIST"

    def run(self, ctx: AuditContext) -> list[Finding]:
        threshold = ctx.config.effective(self.rule_id).threshold.max_params
        method_lines = _method_lines(ctx.tree)
        out: list[Finding] = []
        for fn in _functions(ctx.tree):
            a = fn.args
            count = len(a.posonlyargs) + len(a.args) + len(a.kwonlyargs)
            if fn.lineno in method_lines:
                count -= 1  # self/cls
            if count > threshold:
                out.append(
                    self.make_finding(
                        ctx,
                        line=fn.lineno,
                        message=f"`{fn.name}` takes {count} parameters (> {threshold}); group into an object",
                        suggestion="bundle cohesive params into a dataclass/model",
                    )
                )
        return out


class GodClass(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-GOD-CLASS"

    def run(self, ctx: AuditContext) -> list[Finding]:
        eff = ctx.config.effective(self.rule_id).threshold
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = [
                s
                for s in node.body
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not s.name.startswith("__")
            ]
            attrs = _instance_attrs(node)
            if len(methods) > eff.max_methods or len(attrs) > eff.max_attrs:
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"god class `{node.name}`: {len(methods)} methods, {len(attrs)} attributes",
                        suggestion="split responsibilities into collaborating classes",
                    )
                )
        return out


def _instance_attrs(cls: ast.ClassDef) -> set[str]:
    attrs: set[str] = set()
    for node in ast.walk(cls):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Attribute)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "self"
                ):
                    attrs.add(tgt.attr)
    return attrs


_BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
)


class HighComplexity(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-HIGH-COMPLEXITY"

    def run(self, ctx: AuditContext) -> list[Finding]:
        threshold = ctx.config.effective(self.rule_id).threshold.max_complexity
        out: list[Finding] = []
        for fn in _functions(ctx.tree):
            score = _complexity(fn)
            if score > threshold:
                out.append(
                    self.make_finding(
                        ctx,
                        line=fn.lineno,
                        message=f"`{fn.name}` cyclomatic complexity {score} (> {threshold})",
                        suggestion="extract helpers; reduce branching",
                    )
                )
        return out


def _complexity(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    score = 1
    for node in ast.walk(fn):
        if isinstance(node, _BRANCH_NODES):
            score += 1
        elif isinstance(node, ast.BoolOp):
            score += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            score += 1 + len(node.ifs)
        elif isinstance(node, ast.IfExp):
            score += 1
    return score


class FreeFnOrchestrator(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-FREE-FN-ORCHESTRATOR"
    checklist_item: ClassVar[int] = 19

    def run(self, ctx: AuditContext) -> list[Finding]:
        names = {
            n.name
            for n in ctx.tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        # The smell is a pipeline threading one value: 3+ functions sharing a param AND
        # calling each other — not independent helpers or CLI handlers.
        for param, group in _functions_by_shared_param(ctx.tree).items():
            if len(group) < 3:
                continue
            callers = sum(
                1
                for fn in group
                if {
                    dotted_name(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)
                }
                & names
            )
            if callers >= 2:
                return [
                    self.make_finding(
                        ctx,
                        line=group[0].lineno,
                        message=f"{len(group)} free functions thread `{param}` between them; use a coordinator class",
                        suggestion="encapsulate the pipeline in an X-Coordinator/Index class that holds the state",
                    )
                ]
        return []


class FieldByFieldCopy(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-FIELD-COPY"
    checklist_item: ClassVar[int] = 11

    def run(self, ctx: AuditContext) -> list[Finding]:
        threshold = ctx.config.effective(self.rule_id).threshold.field_copy_min
        out: list[Finding] = []
        for fn in _functions(ctx.tree):
            for source, count in _field_copies(fn).items():
                if count >= threshold:
                    out.append(
                        self.make_finding(
                            ctx,
                            line=fn.lineno,
                            message=f"`{fn.name}` copies {count} fields from `{source}` one by one; compose or use a `from_*` classmethod",
                            suggestion="add a from_X classmethod that copies once, or compose a shared sub-model",
                        )
                    )
                    break  # one finding per function
        return out


def _field_copies(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, int]:
    """Count `target.attr = source.attr` assignments (same field name) per source object —
    the lazy form of composition (item 11)."""
    counts: dict[str, int] = {}
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target, value = node.targets[0], node.value
        if (
            isinstance(target, ast.Attribute)
            and isinstance(value, ast.Attribute)
            and target.attr == value.attr
        ):
            source = _root_name(value.value)
            if source is not None:
                counts[source] = counts.get(source, 0) + 1
    return counts


def _root_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _root_name(node.value)
    return None


class ParallelSibling(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-PARALLEL-SIBLING"
    checklist_item: ClassVar[int] = 17

    def run(self, ctx: AuditContext) -> list[Finding]:
        # Same-file twins only — cross-file near-twins are PY-XFILE-DUP-FUNCTION's job, so the
        # two never overlap (one is within a file, the other across files).
        by_skeleton: dict[tuple[str, ...], list[tuple[str, int, tuple[str, ...]]]] = (
            defaultdict(list)
        )
        for node in ctx.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                skeleton, literals = _twin_fingerprint(node)
                if len(skeleton) >= _MIN_TWIN_TOKENS:
                    by_skeleton[skeleton].append((node.name, node.lineno, literals))
        out: list[Finding] = []
        for members in by_skeleton.values():
            # parallel siblings = identical structure but the *constants* differ; a same-literals
            # match is a true duplicate (a different rule) not a parameterizable twin.
            if len(members) < 2 or len({lits for _, _, lits in members}) < 2:
                continue
            names = ", ".join(name for name, _, _ in members)
            for name, line, _ in members:
                out.append(
                    self.make_finding(
                        ctx,
                        line=line,
                        message=f"`{name}` is a near-twin of {names} (same structure, only constants differ)",
                        suggestion="unify into one function parameterized by the differing value",
                    )
                )
        return out


def _twin_fingerprint(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A literal-blind structural skeleton plus the literal constants, so two functions with the
    same skeleton but different constants are recognizable as parameterizable twins (item 17)."""
    skeleton: list[str] = []
    literals: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant):
            skeleton.append("L")
            literals.append(repr(node.value))
        elif isinstance(node, ast.Call):
            skeleton.append("c:" + dotted_name(node.func))
        elif isinstance(node, ast.Attribute):
            skeleton.append("a:" + node.attr)
        elif isinstance(node, _TWIN_STRUCT):
            skeleton.append(type(node).__name__)
    return tuple(skeleton), tuple(literals)


def _functions_by_shared_param(
    tree: ast.Module,
) -> dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Group top-level functions by a parameter name they share (the threaded state)."""
    groups: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        a = node.args
        for p in a.posonlyargs + a.args + a.kwonlyargs:
            if p.arg in ("self", "cls"):
                continue
            groups.setdefault(p.arg, []).append(node)
    return groups


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


def _mutates_and_returns(fn: ast.FunctionDef | ast.AsyncFunctionDef, param: str) -> bool:
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
    _MIN_CONSTS: ClassVar[int] = 2

    def run(self, ctx: AuditContext) -> list[Finding]:
        consts = _module_const_names(ctx.tree)
        out: list[Finding] = []
        for node in ctx.tree.body:
            if not (isinstance(node, ast.ClassDef) and _has_real_base(node)):
                continue
            prefix = _pascal_to_upper_snake(node.name)
            owned = [c for c in consts if c.lstrip("_").startswith(prefix + "_")]
            if len(owned) >= self._MIN_CONSTS:
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
        out: list[Finding] = []
        for outer in _functions(ctx.tree):
            outer_locals = _bound_names(outer)
            for inner in outer.body:
                if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
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
