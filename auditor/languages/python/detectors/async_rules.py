"""Async-category detectors: sync I/O in async, unlocked lazy init, dangling tasks,
sequential awaits, no-await async bodies."""

import ast
from collections.abc import Iterator
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import dotted_name
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


class UnlockedLazyInit(Detector):
    rule_id: ClassVar[str] = "PY-ASYNC-UNLOCKED-LAZY-INIT"
    category: ClassVar[Category] = Category.ASYNC
    default_severity: ClassVar[Severity] = Severity.HIGH
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 30

    def run(self, ctx: AuditContext) -> list[Finding]:
        locked = _ifs_under_lock(ctx.tree)
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.If):
                continue
            attr = _is_none_check(node.test)
            if attr is None:
                continue
            if _assigns_attr(node.body, attr) and id(node) not in locked:
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"check-then-set lazy init of `self.{attr}` without a lock (race)",
                        suggestion="eager init or double-checked locking with threading.Lock",
                    )
                )
        return out


def _is_none_check(test: ast.expr) -> str | None:
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Is)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
        and isinstance(test.left, ast.Attribute)
        and isinstance(test.left.value, ast.Name)
        and test.left.value.id == "self"
    ):
        return test.left.attr
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
        for loop in ast.walk(ctx.tree):
            if not isinstance(loop, (ast.For, ast.AsyncFor)):
                continue
            awaits = [n for n in ast.walk(loop) if isinstance(n, ast.Await)]
            if awaits:
                out.append(
                    self.make_finding(
                        ctx,
                        line=loop.lineno,
                        message="await inside a loop; if independent, gather them concurrently",
                        suggestion="collect coroutines and await asyncio.gather(*...)",
                    )
                )
        return out


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
        for node in _nodes_excluding_nested_funcs(stmt):
            if isinstance(node, (ast.Await, ast.AsyncWith, ast.AsyncFor)):
                return True
    return False


def _is_async_generator(fn: ast.AsyncFunctionDef) -> bool:
    for stmt in fn.body:
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
