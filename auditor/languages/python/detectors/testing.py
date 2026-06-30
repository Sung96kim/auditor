"""Structural pytest test-quality rules.

These occupy the semantic gap linters and pytest can't reach (parametrize candidates,
logic-in-tests, no-assertion, over-mocking, duplicate setup, unused fixtures, skip-without-
reason, sleep-in-test). All are ``candidate`` (judgment calls) and gated to test-role files.
The repo-level ``UNUSED-FIXTURE`` rule is computed by the cross-file pass (see crossfile.py).
"""

import ast
from abc import abstractmethod
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import (
    dotted_name,
    import_alias_map,
    resolve_dotted,
    test_functions,
)
from auditor.languages.python.shapes import clone_signature
from auditor.models import Category, Finding, Severity, VerdictKind


class PytestRule(Detector):
    """Per-file pytest rule: gated to test roles, ``testing`` category, ``pytest`` framework."""

    abstract: ClassVar[bool] = True
    category: ClassVar[Category] = Category.TESTING
    framework: ClassVar[str | None] = "pytest"
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE

    def run(self, ctx: AuditContext) -> list[Finding]:
        if not ctx.role.is_test:
            return []
        return self.check(ctx)

    @abstractmethod
    def check(self, ctx: AuditContext) -> list[Finding]:
        raise NotImplementedError


# --- C: NO-ASSERTION ---------------------------------------------------------------------

_RAISE_CMS = {"raises", "warns", "deprecated_call"}


def _has_assertion(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            tail = dotted_name(node.func).split(".")[-1]
            if (
                tail.startswith("assert")
                or tail in {"assert_that", "fail"}
                or tail in _RAISE_CMS
            ):
                return True
    return False


def _calls_local_helper(fn: ast.AST, local_funcs: set[str]) -> bool:
    """The test calls a module-local function — assertions may live there (invisible statically)."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in local_funcs
        for node in ast.walk(fn)
    )


class NoAssertion(PytestRule):
    rule_id: ClassVar[str] = "PY-TEST-NO-ASSERTION"
    default_severity: ClassVar[Severity] = Severity.MEDIUM

    def check(self, ctx: AuditContext) -> list[Finding]:
        local = {
            n.name
            for n in ctx.tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        out: list[Finding] = []
        for fn in test_functions(ctx.tree):
            if _has_assertion(fn) or _calls_local_helper(fn, local):
                continue
            out.append(
                self.make_finding(
                    ctx,
                    line=fn.lineno,
                    message=f"test `{fn.name}` makes no assertion — it can't fail",
                    suggestion="assert on the result, or use pytest.raises / a mock assertion",
                )
            )
        return out


# --- B: LOGIC-IN-TEST --------------------------------------------------------------------

_CONTROL = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try)


def _first_logic(fn: ast.AST) -> ast.stmt | None:
    """Earliest control-flow statement in the test body, not descending into nested defs."""
    found: list[ast.stmt] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, _CONTROL):
                found.append(child)
            visit(child)

    visit(fn)
    return min(found, key=lambda n: n.lineno) if found else None


class LogicInTest(PytestRule):
    rule_id: ClassVar[str] = "PY-TEST-LOGIC-IN-TEST"

    def check(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for fn in test_functions(ctx.tree):
            node = _first_logic(fn)
            if node is not None:
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"control flow in test `{fn.name}` — tests should be straight-line",
                        suggestion="split cases into separate tests or @pytest.mark.parametrize",
                    )
                )
        return out


# --- H: SLEEP ----------------------------------------------------------------------------


def _sleep_names(tree: ast.Module) -> set[str]:
    """Local names bound to ``time.sleep`` via ``from time import sleep [as s]``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "time":
            names.update(a.asname or a.name for a in node.names if a.name == "sleep")
    return names


def _is_time_sleep(
    call: ast.Call, sleep_names: set[str], aliases: dict[str, str]
) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in sleep_names
    if isinstance(func, ast.Attribute) and func.attr == "sleep":
        return resolve_dotted(dotted_name(func), aliases) == "time.sleep"
    return False


class SleepInTest(PytestRule):
    rule_id: ClassVar[str] = "PY-TEST-SLEEP"

    def check(self, ctx: AuditContext) -> list[Finding]:
        sleep_names = _sleep_names(ctx.tree)
        aliases = import_alias_map(ctx.tree)
        out: list[Finding] = []
        for fn in test_functions(ctx.tree):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and _is_time_sleep(
                    node, sleep_names, aliases
                ):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message=f"time.sleep() in test `{fn.name}` — flaky by construction",
                            suggestion="wait on a condition or mock the clock instead of sleeping",
                        )
                    )
        return out


# --- G: SKIP-NO-REASON -------------------------------------------------------------------

_SKIP_MARKS = ("skip", "skipif", "xfail")


def _deco_dotted(deco: ast.expr) -> str:
    return dotted_name(deco.func if isinstance(deco, ast.Call) else deco)


def _skip_mark(deco: ast.expr) -> str | None:
    dotted = _deco_dotted(deco)
    return next((m for m in _SKIP_MARKS if dotted.endswith(f"mark.{m}")), None)


def _has_reason(deco: ast.expr, mark: str) -> bool:
    if not isinstance(deco, ast.Call):
        return False  # bare @pytest.mark.skip
    if any(k.arg == "reason" for k in deco.keywords):
        return True
    return mark == "skip" and bool(deco.args)  # skip's first positional IS the reason


def _skip_targets(tree: ast.Module) -> list[ast.AST]:
    """Things a skip mark can decorate: test functions + Test* classes."""
    targets: list[ast.AST] = list(test_functions(tree))
    targets.extend(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name.startswith("Test")
    )
    return targets


class SkipNoReason(PytestRule):
    rule_id: ClassVar[str] = "PY-TEST-SKIP-NO-REASON"

    def check(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in _skip_targets(ctx.tree):
            for deco in node.decorator_list:
                mark = _skip_mark(deco)
                if mark and not _has_reason(deco, mark):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=deco.lineno,
                            message=f"`{mark}` on `{node.name}` has no reason=",
                            suggestion="add reason=… so skipped/xfailed tests are self-documenting",
                        )
                    )
        return out


# --- D: OVER-MOCKING ---------------------------------------------------------------------

_MOCK_TAILS = {"Mock", "MagicMock", "AsyncMock", "patch"}


def _is_mock_call(call: ast.Call) -> bool:
    segs = dotted_name(call.func).split(".")
    if segs[-1] in _MOCK_TAILS:
        return True
    return segs[-1] == "object" and "patch" in segs  # patch.object(...)


def _mock_count(fn: ast.AST) -> int:
    return sum(
        1 for node in ast.walk(fn) if isinstance(node, ast.Call) and _is_mock_call(node)
    )


class OverMocking(PytestRule):
    rule_id: ClassVar[str] = "PY-TEST-OVER-MOCKING"

    def check(self, ctx: AuditContext) -> list[Finding]:
        limit = ctx.config.effective(self.rule_id).threshold.test.max_mocks_per_test
        out: list[Finding] = []
        for fn in test_functions(ctx.tree):
            count = _mock_count(fn)
            if count > limit:
                out.append(
                    self.make_finding(
                        ctx,
                        line=fn.lineno,
                        message=f"test `{fn.name}` builds {count} mocks (> {limit}) — testing mocks, not behavior",
                        suggestion="use real collaborators or a fixture; mock only true boundaries",
                    )
                )
        return out


# --- A: PARAMETRIZE-CANDIDATE ------------------------------------------------------------


def _has_parametrize(fn: ast.AST) -> bool:
    return any("mark.parametrize" in _deco_dotted(d) for d in fn.decorator_list)


def _body_signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return "".join(clone_signature(s) for s in fn.body)


class ParametrizeCandidate(PytestRule):
    rule_id: ClassVar[str] = "PY-TEST-PARAMETRIZE-CANDIDATE"
    default_severity: ClassVar[Severity] = Severity.MEDIUM

    def check(self, ctx: AuditContext) -> list[Finding]:
        eff = ctx.config.effective(self.rule_id).threshold.test
        groups: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        for fn in test_functions(ctx.tree):
            if _has_parametrize(fn) or len(fn.body) < eff.parametrize_min_statements:
                continue
            groups.setdefault(_body_signature(fn), []).append(fn)
        out: list[Finding] = []
        for fns in groups.values():
            if len(fns) < eff.parametrize_min_clones:
                continue
            first = min(fns, key=lambda f: f.lineno)
            names = ", ".join(f.name for f in sorted(fns, key=lambda f: f.lineno))
            out.append(
                self.make_finding(
                    ctx,
                    line=first.lineno,
                    message=f"{len(fns)} tests differ only in literals ({names}) — use @pytest.mark.parametrize",
                    suggestion="collapse the near-identical tests into one parametrized test",
                )
            )
        return out


# --- E: DUPLICATE-SETUP ------------------------------------------------------------------


class DuplicateSetup(PytestRule):
    rule_id: ClassVar[str] = "PY-TEST-DUPLICATE-SETUP"

    def check(self, ctx: AuditContext) -> list[Finding]:
        eff = ctx.config.effective(self.rule_id).threshold.test
        k = eff.setup_min_statements
        candidates = [fn for fn in test_functions(ctx.tree) if len(fn.body) > k]
        # Functions whose *whole* body clones >= parametrize_min_clones peers belong to
        # PARAMETRIZE; drop them so E never re-flags what A already reports (spec invariant).
        by_body: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        for fn in candidates:
            by_body.setdefault(_body_signature(fn), []).append(fn)
        a_claimed = {
            id(fn)
            for grp in by_body.values()
            if len(grp) >= eff.parametrize_min_clones
            for fn in grp
        }
        groups: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        for fn in candidates:
            if id(fn) in a_claimed:
                continue
            prefix = "".join(clone_signature(s) for s in fn.body[:k])
            groups.setdefault(prefix, []).append(fn)
        out: list[Finding] = []
        for fns in groups.values():
            if len(fns) < eff.setup_min_tests:
                continue
            if len({_body_signature(f) for f in fns}) == 1:
                continue  # identical full bodies -> PARAMETRIZE handles it, not a fixture
            first = min(fns, key=lambda f: f.lineno)
            names = ", ".join(f.name for f in sorted(fns, key=lambda f: f.lineno))
            out.append(
                self.make_finding(
                    ctx,
                    line=first.lineno,
                    message=f"{len(fns)} tests share the same {k}-statement setup ({names}) — extract a fixture",
                    suggestion="move the repeated arrange block into a @pytest.fixture",
                )
            )
        return out


# --- I: FIXTURE-MUTABLE-WIDE-SCOPE -------------------------------------------------------

_WIDE_SCOPES = {"session", "module", "package"}


def _fixture_scope(deco: ast.expr) -> str | None:
    """The declared scope of an ``@pytest.fixture``/``@fixture`` decorator (``"function"`` when
    unspecified), or None when ``deco`` is not a fixture decorator at all."""
    if not _deco_dotted(deco).endswith("fixture"):
        return None
    if isinstance(deco, ast.Call):
        scope = next((k.value for k in deco.keywords if k.arg == "scope"), None)
        if isinstance(scope, ast.Constant) and isinstance(scope.value, str):
            return scope.value
    return "function"


def _mutable_results(fn: ast.AST) -> list[ast.expr]:
    """return/yield values that are mutable literals (``[]``/``{}``/``set()``), not descending
    into nested defs/lambdas — a factory fixture returning an inner function isn't shared state."""
    found: list[ast.expr] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            value = child.value if isinstance(child, (ast.Return, ast.Yield)) else None
            if isinstance(value, (ast.List, ast.Dict, ast.Set)):
                found.append(child)
            visit(child)

    visit(fn)
    return found


class FixtureMutableWideScope(PytestRule):
    rule_id: ClassVar[str] = "PY-TEST-FIXTURE-MUTABLE-WIDE-SCOPE"
    default_severity: ClassVar[Severity] = Severity.MEDIUM

    def check(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            scope = next(
                (
                    s
                    for d in node.decorator_list
                    if (s := _fixture_scope(d)) is not None
                ),
                None,
            )
            if scope not in _WIDE_SCOPES:
                continue
            results = _mutable_results(node)
            if results:
                out.append(
                    self.make_finding(
                        ctx,
                        line=results[0].lineno,
                        message=f"{scope}-scoped fixture `{node.name}` returns a mutable literal — one test mutating it leaks into the rest",
                        suggestion="use function scope for fresh state, or return an immutable value (tuple/frozenset)",
                    )
                )
        return out


# --- F: UNUSED-FIXTURE (repo-level; computed by the crossfile pass) ----------------------


class UnusedFixture(Detector):
    """Repo-level: a pytest fixture defined but never requested. Computed by the crossfile pass
    over fixture shape rows (see auditor/fixture_usage.py); ``run`` is a no-op."""

    rule_id: ClassVar[str] = "PY-TEST-UNUSED-FIXTURE"
    category: ClassVar[Category] = Category.TESTING
    framework: ClassVar[str | None] = "pytest"
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    repo_level: ClassVar[bool] = True

    def run(self, ctx: AuditContext) -> list[Finding]:
        return []
