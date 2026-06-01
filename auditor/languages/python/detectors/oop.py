"""OOP/composition-category detectors: constructor walls, flat-field models, thin
wrappers, builder classes, dispatch ladders, static-method classes, free-function
orchestrators, long parameter lists, god classes, high complexity, dataclass-in-pydantic.

Mostly ``candidate`` (the agent judges); ``PY-OOP-DATACLASS-IN-PYDANTIC`` is auto.
"""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import dotted_name
from auditor.models import Category, Finding, Severity, VerdictKind


class _OopCandidate(Detector):
    abstract: ClassVar[bool] = True
    category: ClassVar[Category] = Category.OOP_COMPOSITION
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    default_severity: ClassVar[Severity] = Severity.LOW


def _functions(tree: ast.AST):
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
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
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
            if len(body) == 1 and isinstance(body[0], ast.Return) and isinstance(body[0].value, ast.Call):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"thin wrapper `{node.name}` delegates one call; consider deleting",
                        suggestion="call the underlying directly, or keep only if it adds meaning",
                    )
                )
        return out


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


_FACTORY_VERBS = {"build", "create", "make", "construct", "produce"}


class BuilderClass(_OopCandidate):
    """Flags a function-with-state: a class that stores inputs in ``__init__`` and exposes a
    single public 'produce one output' method (build/create/make/…). Such a class is better
    expressed as a factory classmethod on the result — ``Result.from_X(...)``."""

    rule_id: ClassVar[str] = "PY-OOP-BUILDER-CLASS"
    checklist_item: ClassVar[int] = 9

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = [s for s in node.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
            public = [m.name for m in methods if not m.name.startswith("_")]
            has_init = any(m.name == "__init__" for m in methods)
            named_builder = node.name.endswith("Builder")
            # one public "produce" method, and the class either holds state or is *Builder
            if len(public) == 1 and public[0] in _FACTORY_VERBS and (has_init or named_builder):
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
            methods = [s for s in node.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if methods and all(
                any(dotted_name(d).split(".")[-1] == "staticmethod" for d in m.decorator_list)
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
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and not s.name.startswith("__")
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
        top = [n for n in ctx.tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if len(top) < 3:
            return []
        names = {fn.name for fn in top}
        callers = 0
        for fn in top:
            called = {dotted_name(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)}
            if called & names:
                callers += 1
        if callers >= 2:
            return [
                self.make_finding(
                    ctx,
                    line=top[0].lineno,
                    message=f"{len(top)} free functions thread shared state; consider a coordinator class",
                    suggestion="encapsulate the chain in an X-Coordinator/Index class",
                )
            ]
        return []
