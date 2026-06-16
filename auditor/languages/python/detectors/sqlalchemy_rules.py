"""Framework-aware SQLAlchemy ORM rules (framework="sqlalchemy").

Per-file, local-AST. Each rule is gated to files that import sqlalchemy, so generic names
(`Column`, `text`) never false-positive in non-SA code. Concerns map to existing categories
(correctness / security / async); the SQLAlchemy grouping is the `framework` tag.
"""

import ast
from abc import abstractmethod
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import (
    async_function_bodies,
    is_const_false,
    kwarg,
)
from auditor.models import Category, Finding, Severity, VerdictKind

_COLUMN = {"mapped_column", "Column"}
_EMPTY_CTORS = {"list", "dict", "set"}


def _imports_sqlalchemy(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "sqlalchemy" for a in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] == "sqlalchemy"
        ):
            return True
    return False


def _sa_from_aliases(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] == "sqlalchemy"
        ):
            for a in node.names:
                out[a.asname or a.name] = a.name
    return out


def _tail(call: ast.Call, from_aliases: dict[str, str]) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return from_aliases.get(f.id, f.id)
    return ""


class SqlAlchemyRule(Detector):
    """Per-file SQLAlchemy rule: gated to files importing sqlalchemy."""

    abstract: ClassVar[bool] = True
    framework: ClassVar[str | None] = "sqlalchemy"
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE

    def run(self, ctx: AuditContext) -> list[Finding]:
        if not _imports_sqlalchemy(ctx.tree):
            return []
        return self.check(ctx, _sa_from_aliases(ctx.tree))

    @abstractmethod
    def check(self, ctx: AuditContext, aliases: dict[str, str]) -> list[Finding]:
        raise NotImplementedError


# --- SA-MUTABLE-DEFAULT ------------------------------------------------------------------


def _is_mutable_default(value: ast.expr | None) -> bool:
    if isinstance(value, (ast.List, ast.Dict, ast.Set)):
        return True
    # empty-constructor calls: list()/dict()/set() (a single shared object), but NOT bare callables
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in _EMPTY_CTORS
        and not value.args
        and not value.keywords
    )


class MutableDefault(SqlAlchemyRule):
    rule_id: ClassVar[str] = "SA-MUTABLE-DEFAULT"
    category: ClassVar[Category] = Category.CORRECTNESS
    default_severity: ClassVar[Severity] = Severity.MEDIUM

    def check(self, ctx: AuditContext, aliases: dict[str, str]) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if (
                isinstance(node, ast.Call)
                and _tail(node, aliases) in _COLUMN
                and _is_mutable_default(kwarg(node, "default"))
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="mutable default on a column is shared across rows — use a callable (e.g. `default=list`)",
                        suggestion="pass the callable (`default=list`), not a literal (`default=[]`)",
                    )
                )
        return out


# --- SA-LAZY-DYNAMIC ---------------------------------------------------------------------

_BAD_LAZY = {"dynamic", "subquery"}


class LazyDynamic(SqlAlchemyRule):
    rule_id: ClassVar[str] = "SA-LAZY-DYNAMIC"
    category: ClassVar[Category] = Category.CORRECTNESS
    default_severity: ClassVar[Severity] = Severity.LOW

    def check(self, ctx: AuditContext, aliases: dict[str, str]) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call) and _tail(node, aliases) == "relationship":
                lz = kwarg(node, "lazy")
                if isinstance(lz, ast.Constant) and lz.value in _BAD_LAZY:
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message=f"relationship(lazy={lz.value!r}) is incompatible with async SQLAlchemy / legacy",
                            suggestion='use "selectin" (eager) or load explicitly per query',
                        )
                    )
        return out


# --- SA-IMPLICIT-LAZY-ASYNC --------------------------------------------------------------


class ImplicitLazyAsync(SqlAlchemyRule):
    """relationship() with no explicit lazy= → defaults to "select", which emits a SELECT on
    attribute access. Under AsyncSession that lazy load raises MissingGreenlet. Dormant unless
    the project declares it runs async sessions ([tool.auditor.sqlalchemy] async_session=True),
    since the model file can't reveal whether the session is async (the factory lives elsewhere)."""

    rule_id: ClassVar[str] = "SA-IMPLICIT-LAZY-ASYNC"
    category: ClassVar[Category] = Category.ASYNC
    default_severity: ClassVar[Severity] = Severity.MEDIUM

    def check(self, ctx: AuditContext, aliases: dict[str, str]) -> list[Finding]:
        if not ctx.config.settings.sqlalchemy.async_session:
            return []  # dormant unless the project declares async sessions
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if (
                isinstance(node, ast.Call)
                and _tail(node, aliases) == "relationship"
                and kwarg(node, "lazy") is None
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="relationship() with no explicit lazy= defaults to a sync SELECT on access — MissingGreenlet under AsyncSession",
                        suggestion='set lazy explicitly: "selectin" (eager) or "raise" to forbid implicit IO',
                    )
                )
        return out


# --- SA-JOINED-COLLECTION ----------------------------------------------------------------

_COLLECTION_TYPES = {
    "list",
    "List",
    "set",
    "Set",
    "frozenset",
    "Sequence",
    "Collection",
    "MutableSequence",
}


def _is_collection_annotation(ann: ast.expr | None) -> bool:
    """True if the annotation subscripts a collection type (``Mapped[list[X]]``, ``list[X]``,
    ``List[X]`` …) — a to-many relationship, where lazy="joined" produces a cartesian product."""
    if ann is None:
        return False
    return any(
        isinstance(n, ast.Subscript)
        and isinstance(n.value, ast.Name)
        and n.value.id in _COLLECTION_TYPES
        for n in ast.walk(ann)
    )


class JoinedCollection(SqlAlchemyRule):
    """relationship(lazy="joined") on a to-many (collection) relationship → a JOIN that multiplies
    parent rows by children (cartesian blowup); docs prefer selectin for collections."""

    rule_id: ClassVar[str] = "SA-JOINED-COLLECTION"
    category: ClassVar[Category] = Category.CORRECTNESS
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.AUTO

    def check(self, ctx: AuditContext, aliases: dict[str, str]) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not (
                isinstance(node, ast.AnnAssign)
                and _is_collection_annotation(node.annotation)
            ):
                continue
            call = node.value
            if not (
                isinstance(call, ast.Call) and _tail(call, aliases) == "relationship"
            ):
                continue
            lz = kwarg(call, "lazy")
            if isinstance(lz, ast.Constant) and lz.value == "joined":
                out.append(
                    self.make_finding(
                        ctx,
                        line=call.lineno,
                        message='relationship(lazy="joined") on a collection — cartesian-product JOIN multiplies parent rows',
                        suggestion='use lazy="selectin" for to-many relationships (joined is for many-to-one)',
                    )
                )
        return out


# --- SA-NAIVE-DATETIME-DEFAULT -----------------------------------------------------------


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return _dotted(node.value) + "." + node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _is_naive_default(value: ast.expr | None) -> bool:
    if value is None:
        return False
    if _dotted(value).endswith("utcnow"):  # datetime.utcnow / datetime.datetime.utcnow
        return True
    # func.now() / sa.func.now()
    return isinstance(value, ast.Call) and _dotted(value).endswith("func.now")


class NaiveDatetimeDefault(SqlAlchemyRule):
    rule_id: ClassVar[str] = "SA-NAIVE-DATETIME-DEFAULT"
    category: ClassVar[Category] = Category.CORRECTNESS
    default_severity: ClassVar[Severity] = Severity.LOW

    def check(self, ctx: AuditContext, aliases: dict[str, str]) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call) and _tail(node, aliases) in _COLUMN:
                d = kwarg(node, "default")
                if _is_naive_default(d) and kwarg(node, "server_default") is None:
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message="naive/`server_default`-less datetime column default — rows inserted outside the ORM get NULL / naive timestamps",
                            suggestion="use a tz-aware callable and add `server_default=func.now()`",
                        )
                    )
        return out


# --- SA-RAW-SQL --------------------------------------------------------------------------

_RAW_FUNCS = {"text", "execute"}


#: calls whose result is always numeric (never str), so interpolating one into raw SQL can't carry
#: an injection payload. `hex`/`oct`/`bin` are excluded — they return str.
_NUMERIC_CALLS = {"len", "int", "float", "round", "abs", "ord", "hash", "id"}


def _is_non_str(expr: ast.expr) -> bool:
    """The value is provably not a ``str`` — a numeric literal, a numeric-returning call
    (``len``/``int``/…), or arithmetic over such. Such a value can't inject SQL."""
    if isinstance(expr, ast.Constant):
        return isinstance(expr.value, (int, float, complex))  # bool is an int subclass
    if isinstance(expr, ast.Call):
        f = expr.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        return name in _NUMERIC_CALLS
    if isinstance(expr, ast.UnaryOp):
        return _is_non_str(expr.operand)
    if isinstance(expr, ast.BinOp):
        return _is_non_str(expr.left) and _is_non_str(expr.right)
    return False


def _is_interpolated(arg: ast.expr | None) -> bool:
    if isinstance(arg, ast.JoinedStr):
        # injectable only if some interpolated value isn't provably numeric:
        # `text(f"… {len(rows)}")` is safe, `text(f"… {name}")` is not.
        return any(
            not _is_non_str(v.value)
            for v in arg.values
            if isinstance(v, ast.FormattedValue)
        )
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
        return not (
            isinstance(arg.left, ast.Constant) and isinstance(arg.right, ast.Constant)
        )
    return False


class RawSql(SqlAlchemyRule):
    rule_id: ClassVar[str] = "SA-RAW-SQL"
    category: ClassVar[Category] = Category.SECURITY
    default_severity: ClassVar[Severity] = Severity.HIGH

    def check(self, ctx: AuditContext, aliases: dict[str, str]) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if (
                isinstance(node, ast.Call)
                and _tail(node, aliases) in _RAW_FUNCS
                and node.args
                and _is_interpolated(node.args[0])
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="raw SQL built with string interpolation — SQL-injection risk",
                        suggestion="use bound parameters (`text('… :p').bindparams(p=…)`), never f-strings/concat",
                    )
                )
        return out


# --- SA-ASYNC-EXPIRE-ON-COMMIT -----------------------------------------------------------


def _is_async_factory(call: ast.Call, tail: str) -> bool:
    if tail == "async_sessionmaker":
        return True
    if tail == "sessionmaker":
        cls = kwarg(call, "class_")
        return cls is not None and _dotted(cls).endswith("AsyncSession")
    return False


class AsyncExpireOnCommit(SqlAlchemyRule):
    rule_id: ClassVar[str] = "SA-ASYNC-EXPIRE-ON-COMMIT"
    category: ClassVar[Category] = Category.ASYNC
    default_severity: ClassVar[Severity] = Severity.MEDIUM

    def check(self, ctx: AuditContext, aliases: dict[str, str]) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if (
                isinstance(node, ast.Call)
                and _is_async_factory(node, _tail(node, aliases))
                and not is_const_false(kwarg(node, "expire_on_commit"))
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="async session factory without expire_on_commit=False — attributes expire after commit → MissingGreenlet",
                        suggestion="pass expire_on_commit=False to the async session factory",
                    )
                )
        return out


# --- SA-GREENLET-ATTR-AFTER-COMMIT -------------------------------------------------------

#: session methods that take a SINGLE ORM object → the Name arg is provably an ORM instance.
#: `add_all` is excluded: its arg is a *collection* (a list), not an ORM object — flagging
#: `links.append(...)` after a commit would be a false positive.
_ORM_SINKS = {"add", "merge", "delete", "refresh"}
_ORM_SOURCES = {"scalar", "scalar_one", "scalar_one_or_none"}


def _orm_names(fn: ast.AST) -> set[str]:
    """Names that are provably ORM objects in this function: passed to session.add/merge/…,
    or assigned from a session query (scalar*, or get(Model, pk) with >=2 args — avoids dict.get)."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            tail = node.func.attr
            if tail in _ORM_SINKS:
                names.update(a.id for a in node.args if isinstance(a, ast.Name))
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
        ):
            tail = node.value.func.attr
            if tail in _ORM_SOURCES or (tail == "get" and len(node.value.args) >= 2):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _commit_lines(fn: ast.AST) -> list[int]:
    return sorted(
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "commit"
    )


def _unconditional_stmts(body: list[ast.stmt]):
    """Statements that always execute: top level + with/try bodies. NOT if/loop bodies."""
    for stmt in body:
        if isinstance(stmt, (ast.With, ast.AsyncWith, ast.Try)):
            yield from _unconditional_stmts(stmt.body)
        else:
            yield stmt


def _refresh_call_arg(stmt: ast.stmt) -> str | None:
    """If ``stmt`` is (await) <x>.refresh(<Name>), return that Name's id."""
    value = stmt.value if isinstance(stmt, ast.Expr) else None
    if isinstance(value, ast.Await):
        value = value.value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "refresh"
        and value.args
        and isinstance(value.args[0], ast.Name)
    ):
        return value.args[0].id
    return None


def _refresh_effects(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[frozenset[int], frozenset[int]]:
    """(params refreshed directly, params whose elements are refreshed) — unconditional only.
    Direct: ``s.refresh(p)``. Elements: ``for o in p: s.refresh(o)`` (covers every element).
    Parameter indices are positional over ``posonlyargs + args``, so for a method ``self``/``cls``
    occupies index 0 (Phase-1 callers only pass module-level functions, so this is not currently hit)."""
    params = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    index = {name: i for i, name in enumerate(params)}
    direct: set[int] = set()
    elements: set[int] = set()
    for stmt in _unconditional_stmts(fn.body):
        name = _refresh_call_arg(stmt)
        if name in index:
            direct.add(index[name])
        elif (
            isinstance(stmt, (ast.For, ast.AsyncFor))
            and isinstance(stmt.iter, ast.Name)
            and stmt.iter.id in index
            and isinstance(stmt.target, ast.Name)
            and any(
                _refresh_call_arg(s) == stmt.target.id
                for s in _unconditional_stmts(stmt.body)
            )
        ):
            elements.add(index[stmt.iter.id])
    return frozenset(direct), frozenset(elements)


def _name_elements(arg: ast.expr) -> list[str]:
    """Name ids inside a list/tuple/set literal (incl. ``*starred``); a bare Name → [name]; else []."""
    if isinstance(arg, ast.Starred):
        return _name_elements(arg.value)
    if isinstance(arg, ast.Name):
        return [arg.id]
    if isinstance(arg, (ast.List, ast.Tuple, ast.Set)):
        return [n for e in arg.elts for n in _name_elements(e)]
    return []


def _add_resolved_freshen(
    fn: ast.AST, ctx: AuditContext, freshen: dict[str, list[int]]
) -> None:
    """For each call whose resolved def unconditionally refreshes a parameter, mark the object(s)
    passed at that position as freshened at the call line. No resolver / unresolved → no-op."""
    resolver = getattr(ctx, "resolver", None)
    if resolver is None:
        return
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        callee = resolver.resolve_func(node, ctx.tree)
        if callee is None:
            continue
        direct, elements = _refresh_effects(callee)
        names: list[str] = []
        for i in direct:
            if i < len(node.args) and isinstance(node.args[i], ast.Name):
                names.append(node.args[i].id)
        for i in elements:
            if i < len(node.args):
                names.extend(_name_elements(node.args[i]))
        for name in names:
            freshen.setdefault(name, []).append(node.lineno)


def _freshen_lines(fn: ast.AST) -> dict[str, list[int]]:
    """Per-name lines where a name is (re)bound or reloaded — an assignment (construction or
    query), a for-loop target, or ``session.refresh(obj)``. A commit only expires an object's
    attributes if it happens AFTER the object was last freshened; an earlier commit is irrelevant
    (the object didn't exist / was reloaded since), and ``commit(); refresh(obj); use obj`` is safe."""
    out: dict[str, list[int]] = {}

    def add(name: str, line: int) -> None:
        out.setdefault(name, []).append(line)

    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    add(t.id, node.lineno)
        elif isinstance(node, (ast.AnnAssign, ast.For, ast.AsyncFor)) and isinstance(
            node.target, ast.Name
        ):
            add(node.target.id, node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "refresh"
        ):
            for a in node.args:
                if isinstance(a, ast.Name):
                    add(a.id, node.lineno)
    return out


class GreenletAttrAfterCommit(SqlAlchemyRule):
    rule_id: ClassVar[str] = "SA-GREENLET-ATTR-AFTER-COMMIT"
    category: ClassVar[Category] = Category.ASYNC
    default_severity: ClassVar[Severity] = Severity.MEDIUM

    def check(self, ctx: AuditContext, aliases: dict[str, str]) -> list[Finding]:
        if not ctx.config.settings.sqlalchemy.expire_on_commit:
            return []  # dormant unless the project declares expire_on_commit=True
        if ctx.role.is_test:
            return []  # the running-app risk, not test setup (tests configure their own sessions)
        out: list[Finding] = []
        for fn in async_function_bodies(ctx.tree):
            commits = _commit_lines(fn)
            if not commits:
                continue
            orm = _orm_names(fn)
            freshen = _freshen_lines(fn)
            _add_resolved_freshen(fn, ctx, freshen)
            for node in ast.walk(fn):
                if not (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.ctx, ast.Load)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in orm
                ):
                    continue
                # the object is "fresh" as of its last bind/reload before the access (params have
                # none → fresh from the function start); a commit strictly between then and the
                # access is what expires it.
                fresh = max(
                    (f for f in freshen.get(node.value.id, ()) if f < node.lineno),
                    default=fn.lineno,
                )
                if not any(fresh < c < node.lineno for c in commits):
                    continue
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"`{node.value.id}.{node.attr}` accessed after commit — expired attribute reload → MissingGreenlet",
                        suggestion="capture needed values before commit, or refresh(obj) / set expire_on_commit=False",
                    )
                )
        return out
