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
            ladder = _dispatch_ladder(fn, threshold)
            if ladder is not None:
                line, detail = ladder
                out.append(
                    self.make_finding(
                        ctx,
                        line=line,
                        message=f"dispatch ladder ({detail})",
                        suggestion="replace the ladder with a registered subclass family or a dict dispatch",
                    )
                )
        return out


def _dispatch_ladder(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, threshold: int
) -> tuple[int, str] | None:
    """A dispatch ladder in either form: an ``if/elif`` chain, or ≥ ``threshold`` sibling
    guard-clause ``if``s in one block all keying off the same discriminator variable."""
    for stmt in ast.walk(fn):
        if isinstance(stmt, ast.If):
            branches = _elif_chain_len(stmt)
            if branches >= threshold:
                return stmt.lineno, f"{branches} if/elif branches"
    for block in _statement_blocks(fn):
        by_disc: dict[str, list[int]] = defaultdict(list)
        for s in block:
            if isinstance(s, ast.If) and (disc := _discriminator(s.test)) is not None:
                by_disc[disc].append(s.lineno)
        for disc, lines in by_disc.items():
            if len(lines) >= threshold:
                return min(lines), f"{len(lines)} guard clauses on `{disc}`"
    return None


def _elif_chain_len(node: ast.If) -> int:
    count = 1
    cur = node
    while len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
        count += 1
        cur = cur.orelse[0]
    return count


_DISPATCH_OPS = (ast.Eq, ast.NotEq, ast.In, ast.NotIn)


def _discriminator(test: ast.expr) -> str | None:
    """The variable a guard tests, when it's an equality/membership check (`t == "x"`,
    `t in {...}`) — the thing a dispatch ladder switches on. ``None`` otherwise."""
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], _DISPATCH_OPS)
    ):
        return _name_of(test.left)
    return None


def _name_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


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
        min_tokens = ctx.config.effective(
            self.rule_id
        ).threshold.parallel_sibling_min_tokens
        for node in ctx.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                skeleton, literals = _twin_fingerprint(node)
                if len(skeleton) >= min_tokens:
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
    same skeleton but different constants are recognizable as parameterizable twins (item 17).
    """
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


class DuplicateBlock(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-DUPLICATE-BLOCK"

    def run(self, ctx: AuditContext) -> list[Finding]:
        # Within-file duplication of an AST statement block (a branch/loop/with/try/function
        # body). Cross-file dup is PY-XFILE-DUP-FUNCTION; whole-function twins are
        # PY-OOP-PARALLEL-SIBLING — this catches a block copy-pasted inside one file.
        threshold = ctx.config.effective(self.rule_id).threshold
        min_statements = threshold.dup_block_min_statements
        min_tokens = threshold.dup_block_min_tokens
        groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for block in _statement_blocks(ctx.tree):
            if len(block) < min_statements:
                continue
            tokens = tuple(t for stmt in block for t in _block_tokens(stmt))
            if len(tokens) >= min_tokens:
                groups[tokens].append(block[0].lineno)
        out: list[Finding] = []
        for lines in groups.values():
            unique = sorted(set(lines))
            if len(unique) < 2:
                continue
            elsewhere = ", ".join(f"L{ln}" for ln in unique[1:])
            out.append(
                self.make_finding(
                    ctx,
                    line=unique[0],
                    message=f"this block is duplicated at {elsewhere}; extract a shared helper",
                    suggestion="pull the repeated statements into a function/method and call it",
                )
            )
        return out


def _statement_blocks(tree: ast.AST) -> Iterator[list[ast.stmt]]:
    """Every statement-list body in the tree: function/branch/loop/with/try bodies + handlers."""
    seen: set[int] = set()
    for node in ast.walk(tree):
        blocks = [getattr(node, f, None) for f in ("body", "orelse", "finalbody")]
        blocks += [h.body for h in getattr(node, "handlers", []) or []]
        for block in blocks:
            if (
                isinstance(block, list)
                and block
                and isinstance(block[0], ast.stmt)
                and id(block) not in seen
            ):
                seen.add(id(block))
                yield block


def _block_tokens(stmt: ast.stmt) -> list[str]:
    """Name-aware, literal-blind token stream — identical logic on the same names matches even
    when constants differ; different calls/names do not collide."""
    out: list[str] = []
    for node in ast.walk(stmt):
        if isinstance(node, ast.Constant):
            out.append("L")
        elif isinstance(node, ast.Name):
            out.append("n:" + node.id)
        elif isinstance(node, ast.Attribute):
            out.append("a:" + node.attr)
        else:
            out.append(type(node).__name__)
    return out


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
