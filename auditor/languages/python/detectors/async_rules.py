"""Async-category detectors: sync I/O in async, unlocked lazy init, dangling tasks,
sequential awaits, no-await async bodies."""

import ast
from collections.abc import Iterator
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import (
    decorator_names,
    dotted_name,
    is_route_handler,
)
from auditor.models import Category, Finding, Severity, VerdictKind

_SYNC_IO_NAMES = {
    "time.sleep",
    "os.system",
    "open",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_output",
    "subprocess.check_call",
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "requests.patch",
    "requests.head",
    "requests.request",
}
_SYNC_IO_SUFFIXES = (".read", ".write", ".readlines", ".writelines")


def _own_async_funcs(tree: ast.AST) -> Iterator[ast.AsyncFunctionDef]:
    """Yield async functions, but exclude nested-function descents for ownership checks."""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            yield node


def _direct_calls_in(fn: ast.AsyncFunctionDef) -> Iterator[ast.Call]:
    """Calls inside fn but not inside a nested (sync or async) function def."""
    for stmt in fn.body:
        yield from _calls_excluding_nested(stmt)


def _calls_excluding_nested(node: ast.AST) -> Iterator[ast.Call]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(child, ast.Call):
            yield child
        yield from _calls_excluding_nested(child)


class SyncIoInAsync(Detector):
    rule_id: ClassVar[str] = "PY-ASYNC-SYNC-IO"
    category: ClassVar[Category] = Category.ASYNC
    default_severity: ClassVar[Severity] = Severity.HIGH
    # candidate: detection is name-based (a bare `.read`/`.write`/`open` can't be confirmed as
    # actually-blocking I/O without type info), so the agent judges — see the evidence line.
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 29

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for fn in _own_async_funcs(ctx.tree):
            awaited = {
                id(n.value)
                for n in ast.walk(fn)
                if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
            }
            for call in _direct_calls_in(fn):
                if id(call) in awaited:
                    continue  # an awaited call does not block the loop
                name = dotted_name(call.func)
                if (
                    name in _SYNC_IO_NAMES
                    or name.endswith(_SYNC_IO_SUFFIXES)
                    or name == "httpx.Client"
                ):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=call.lineno,
                            message=f"sync `{name}(...)` blocks the event loop inside async `{fn.name}`",
                            suggestion="use an async equivalent or asyncio.to_thread(...)",
                        )
                    )
        return out


#: calls that create/hold a shared OS resource — a process, socket, connection, or file.
#: The check-then-create form only fires when the fallthrough creates one of these.
_OS_RESOURCE_CALLS = {
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.check_output",
    "subprocess.check_call",
    "os.fork",
    "os.forkpty",
    "multiprocessing.Process",
    "socket.socket",
    "socket.create_connection",
    "open",
    "tempfile.NamedTemporaryFile",
    "tempfile.TemporaryDirectory",
    "tempfile.mkstemp",
    "tempfile.mkdtemp",
}
_OS_RESOURCE_SUFFIXES = (
    ".connect",
    ".Popen",
    ".Process",
    ".write_text",
    ".write_bytes",
    ".touch",
    ".mkdir",
)
#: file-lock calls that make a check-then-create safe (with-`lock` is handled separately)
_LOCK_CALLS = {"fcntl.flock", "fcntl.lockf", "msvcrt.locking"}

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


class UnlockedLazyInit(Detector):
    """Check-then-set/create without a lock, in two forms: (1) ``if self._x is None: self._x = …``
    (also the ``if not self._x:`` truthiness spelling); (2) a method that returns saved state from
    a guard, else calls a creator of a shared OS resource (process/socket/connection/file) — under
    concurrency every cold caller passes the check and creates its own. The GIL does not make
    check-then-set atomic."""

    rule_id: ClassVar[str] = "PY-ASYNC-UNLOCKED-LAZY-INIT"
    category: ClassVar[Category] = Category.ASYNC
    default_severity: ClassVar[Severity] = Severity.HIGH
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    version: ClassVar[str] = "2"
    checklist_item: ClassVar[int] = 30

    def run(self, ctx: AuditContext) -> list[Finding]:
        locked = _ifs_under_lock(ctx.tree)
        out: list[Finding] = []
        flagged: set[int] = set()
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.If):
                continue
            attr = _lazy_attr_check(node.test)
            if attr is None:
                continue
            if _assigns_attr(node.body, attr) and id(node) not in locked:
                flagged.add(id(node))
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"check-then-set lazy init of `self.{attr}` without a lock (race)",
                        suggestion="eager init or double-checked locking with threading.Lock",
                    )
                )
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.ClassDef):
                out.extend(self._check_then_create(ctx, node, locked, flagged))
        return out

    def _check_then_create(
        self,
        ctx: AuditContext,
        cls: ast.ClassDef,
        locked: set[int],
        flagged: set[int],
    ) -> list[Finding]:
        methods = [s for s in cls.body if isinstance(s, _FuncDef)]
        creators = {m.name for m in methods if _calls_os_resource(m)}
        lockers = {m.name for m in methods if _uses_lock(m)}
        out: list[Finding] = []
        for m in methods:
            if m.name == "__init__" or m.name in lockers:
                continue
            hit = _guard_then_create(m, creators)
            if hit is None:
                continue
            guard, creator = hit
            if id(guard) in locked or id(guard) in flagged:
                continue
            note = "; a sibling method in this class does lock" if lockers else ""
            out.append(
                self.make_finding(
                    ctx,
                    line=guard.lineno,
                    message=(
                        f"`{m.name}` checks saved state then calls `{creator}(...)` (creates an OS "
                        f"resource) without a lock — concurrent cold callers each create one{note}"
                    ),
                    suggestion="double-checked locking (threading.Lock / fcntl.flock), or create eagerly in __init__",
                )
            )
        return out


def _lazy_attr_check(test: ast.expr) -> str | None:
    """The self-attribute a lazy-init guard tests: ``self._x is None`` or ``not self._x``."""
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Is)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    ):
        return _self_attr(test.left)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _self_attr(test.operand)
    return None


def _self_attr(node: ast.expr) -> str | None:
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    return None


def _is_os_resource_call(name: str) -> bool:
    return name in _OS_RESOURCE_CALLS or name.endswith(_OS_RESOURCE_SUFFIXES)


def _calls_os_resource(fn: _FuncDef) -> bool:
    return any(
        isinstance(n, ast.Call) and _is_os_resource_call(dotted_name(n.func))
        for n in ast.walk(fn)
    )


def _uses_lock(fn: _FuncDef) -> bool:
    """The method already synchronizes: a ``with …lock…:`` block or a file-lock call."""
    for node in ast.walk(fn):
        if isinstance(node, (ast.With, ast.AsyncWith)) and any(
            "lock" in dotted_name(item.context_expr).lower() for item in node.items
        ):
            return True
        if isinstance(node, ast.Call) and dotted_name(node.func) in _LOCK_CALLS:
            return True
    return False


def _test_reads_self(test: ast.expr) -> bool:
    return any(
        isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
        for n in ast.walk(test)
    )


def _returns_self_state(body: list[ast.stmt]) -> bool:
    """The guard returns something read off ``self`` — the 'already have it' early exit
    (``return self.saved()`` / ``return self._proc``), not a feature-flag ``return None``."""
    return any(
        isinstance(n, ast.Return) and n.value is not None and _test_reads_self(n.value)
        for s in body
        for n in ast.walk(s)
    )


def _guard_then_create(
    m: _FuncDef, creators: set[str]
) -> tuple[ast.If, str] | None:
    """The first ``if <reads self>: return <self state>`` whose fallthrough (or else) calls an
    OS-resource creator — directly, or via a sibling method of the class (``creators``)."""
    for i, stmt in enumerate(m.body):
        if not isinstance(stmt, ast.If) or not _test_reads_self(stmt.test):
            continue
        if not _returns_self_state(stmt.body):
            continue
        for s in [*stmt.orelse, *m.body[i + 1 :]]:
            for n in ast.walk(s):
                if not isinstance(n, ast.Call):
                    continue
                name = dotted_name(n.func)
                if (
                    name.startswith("self.") and name[5:] in creators
                ) or _is_os_resource_call(name):
                    return stmt, name
    return None


def _assigns_attr(body: list[ast.stmt], attr: str) -> bool:
    for stmt in body:
        if isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if (
                    isinstance(tgt, ast.Attribute)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "self"
                    and tgt.attr == attr
                ):
                    return True
    return False


def _ifs_under_lock(tree: ast.AST) -> set[int]:
    """ids of all `if` nodes nested inside a `with ...lock...:` (or async with)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)) and any(
            "lock" in dotted_name(item.context_expr).lower() for item in node.items
        ):
            for desc in ast.walk(node):
                if isinstance(desc, ast.If):
                    out.add(id(desc))
    return out


class DanglingTask(Detector):
    rule_id: ClassVar[str] = "PY-ASYNC-DANGLING-TASK"
    category: ClassVar[Category] = Category.ASYNC
    default_severity: ClassVar[Severity] = Severity.HIGH

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                name = dotted_name(node.value.func)
                if name in ("asyncio.create_task", "asyncio.ensure_future"):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message=f"`{name}(...)` result is discarded; the task may be GC'd mid-flight",
                            suggestion="store the task (e.g. a set) or await it",
                        )
                    )
        return out


class SequentialAwaits(Detector):
    rule_id: ClassVar[str] = "PY-ASYNC-SEQUENTIAL-AWAITS"
    category: ClassVar[Category] = Category.ASYNC
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for loop, cm_names in _loops_with_cm_scope(ctx.tree):
            # only awaits in the loop *body* run per-iteration; an await in the iterable
            # (`for x in (await f()).values():`) is evaluated once — not a gather opportunity
            awaits = [
                n
                for stmt in (*loop.body, *loop.orelse)
                for n in ast.walk(stmt)
                if isinstance(n, ast.Await)
            ]
            if not awaits:
                continue
            # ordered-sink exclusion: if every awaited call writes to a resource bound by an
            # enclosing `with`/`async with` (a file/stream/connection/transaction), the loop is
            # sequential *by necessity* — gathering would corrupt the shared handle, not speed it
            # up. `async for chunk in stream: await f.write(chunk)` is the canonical case.
            if all(_await_receiver(a) in cm_names for a in awaits):
                continue
            out.append(
                self.make_finding(
                    ctx,
                    line=loop.lineno,
                    message="await inside a loop; if independent, gather them concurrently",
                    suggestion="collect coroutines and await asyncio.gather(*...)",
                )
            )
        return out


def _loops_with_cm_scope(
    node: ast.AST, cm_names: frozenset[str] = frozenset()
) -> Iterator[tuple[ast.For | ast.AsyncFor, frozenset[str]]]:
    """Yield every ``(for/async-for loop, names bound by enclosing with/async-with)``. The
    context-managed name set accumulates as we descend into ``with`` bodies, so each loop knows
    which receivers are ordered resources (a file/connection) at its position."""
    if isinstance(node, (ast.For, ast.AsyncFor)):
        yield node, cm_names
    if isinstance(node, (ast.With, ast.AsyncWith)):
        bound = {
            n
            for item in node.items
            if item.optional_vars is not None
            for n in _bound_names(item.optional_vars)
        }
        cm_names = cm_names | bound
    for child in ast.iter_child_nodes(node):
        yield from _loops_with_cm_scope(child, cm_names)


def _bound_names(target: ast.expr) -> list[str]:
    """Names bound by a ``with ... as <target>`` clause — a Name, or a tuple/list of them."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for e in target.elts for n in _bound_names(e)]
    return []


def _await_receiver(await_node: ast.Await) -> str | None:
    """For ``await <recv>.method(...)`` where ``<recv>`` is a bare Name, that name; else None
    (a free call like ``await fetch(x)`` has no receiver → a genuine gather candidate)."""
    value = await_node.value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Name)
    ):
        return value.func.value.id
    return None


class NoAwaitBody(Detector):
    rule_id: ClassVar[str] = "PY-ASYNC-NO-AWAIT-BODY"
    category: ClassVar[Category] = Category.ASYNC
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE

    #: async dunders the language requires to be coroutines even without an await
    _ASYNC_PROTOCOL = {"__aenter__", "__aexit__", "__anext__", "__aiter__", "__acall__"}

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for fn in _own_async_funcs(ctx.tree):
            if fn.name in self._ASYNC_PROTOCOL or _is_abstract_or_stub(fn):
                continue  # protocol coroutines / abstract stubs are legitimately await-free
            if decorator_names(fn) & {"abstractmethod", "abstractproperty"}:
                continue  # abstract: a subclass override needs the async signature
            if is_route_handler(fn):
                continue  # framework-managed signature; async-vs-sync handler is deliberate
            if _is_async_generator(fn):
                continue  # `async def` + yield is an async generator; must stay async
            if not _has_async_construct(fn):
                out.append(
                    self.make_finding(
                        ctx,
                        line=fn.lineno,
                        message=f"async `{fn.name}` has no await/async-with/async-for; make it sync",
                        suggestion="drop `async` if the body never awaits",
                    )
                )
        return out


def _has_async_construct(fn: ast.AsyncFunctionDef) -> bool:
    for stmt in fn.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in _nodes_excluding_nested_funcs(stmt):
            if isinstance(node, (ast.Await, ast.AsyncWith, ast.AsyncFor)):
                return True
    return False


def _is_async_generator(fn: ast.AsyncFunctionDef) -> bool:
    for stmt in fn.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in _nodes_excluding_nested_funcs(stmt):
            if isinstance(node, (ast.Yield, ast.YieldFrom)):
                return True
    return False


def _is_abstract_or_stub(fn: ast.AsyncFunctionDef) -> bool:
    """A stub coroutine — ``...``/``pass``/``raise`` — is legitimately await-free."""
    body = [
        s
        for s in fn.body
        if not (
            isinstance(s, ast.Expr)
            and isinstance(s.value, ast.Constant)
            and isinstance(s.value.value, str)
        )
    ]
    if not body:
        return True
    if len(body) == 1:
        stmt = body[0]
        if isinstance(stmt, (ast.Pass, ast.Raise)):
            return True
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is ...
        ):
            return True
    return False


def _nodes_excluding_nested_funcs(node: ast.AST) -> Iterator[ast.AST]:
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield from _nodes_excluding_nested_funcs(child)


class UnawaitedCoroutine(Detector):
    rule_id: ClassVar[str] = "PY-ASYNC-UNAWAITED-COROUTINE"
    category: ClassVar[Category] = Category.ASYNC
    default_severity: ClassVar[Severity] = Severity.HIGH

    def run(self, ctx: AuditContext) -> list[Finding]:
        module_async = {
            n.name for n in ctx.tree.body if isinstance(n, ast.AsyncFunctionDef)
        }
        out: list[Finding] = []
        for call in _bare_statement_calls(ctx.tree):
            func = call.func
            if isinstance(func, ast.Name) and func.id in module_async:
                out.append(self._finding(ctx, call, f"{func.id}"))
        for cls in ast.walk(ctx.tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            methods = {m.name for m in cls.body if isinstance(m, ast.AsyncFunctionDef)}
            for call in _bare_statement_calls(cls):
                func = call.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "self"
                    and func.attr in methods
                ):
                    out.append(self._finding(ctx, call, f"self.{func.attr}"))
        return out

    def _finding(self, ctx: AuditContext, call: ast.Call, label: str) -> Finding:
        return self.make_finding(
            ctx,
            line=call.lineno,
            message=f"`{label}(...)` is a coroutine that is never awaited; the call silently does nothing",
            suggestion="add `await` (or asyncio.create_task/gather if fire-and-forget is intended)",
        )


def _bare_statement_calls(node: ast.AST) -> Iterator[ast.Call]:
    """Calls that are a bare expression statement — result discarded, not awaited. An awaited
    call is ``Expr(Await(Call))`` so its ``.value`` is an Await, not a Call, and is skipped.
    """
    for n in ast.walk(node):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
            yield n.value
