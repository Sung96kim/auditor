"""React structural detectors that need no design-system knowledge.

The auditor runs on any repo with no knowledge of its primitive vocabulary, so it does NOT
map raw markup to specific primitives ("this should be <Badge>") or enforce project styling
conventions — that requires reading the project's design system first, which is the agent +
frontend-design-system-review skill's job. What stays here is objective structure.
"""

from collections import defaultdict
from typing import ClassVar

from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.nodes import Tsx, callee, is_pascal_case
from auditor.models import Category, Finding, Severity, VerdictKind

_ITER_METHODS = {"map", "forEach", "flatMap"}


class ArrayIndexKey(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-ARRAY-INDEX-KEY"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 13

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for attr in ctx.root.descendants("jsx_attribute"):
            if attr.attr_name() != "key":
                continue
            value = attr.attr_value()
            params = _enclosing_iter_params(attr)
            if value is None or params is None:
                continue
            item, index = params
            idents = {n.text for n in value.walk() if n.type == "identifier"}
            # flag only a *bare* index key; a composite that also uses the item
            # (`key={`${file.name}-${i}`}`) is stable and fine.
            if index in idents and (item is None or item not in idents):
                out.append(
                    self.make_finding(
                        ctx,
                        line=attr.line,
                        message=f"`key={{{index}}}` uses the array index; unstable when the list reorders/inserts",
                        suggestion="key off a stable unique id from the item, not its position",
                    )
                )
        return out


def _enclosing_iter_params(attr: Tsx) -> tuple[str | None, str] | None:
    """(item, index) parameter names of the nearest enclosing ``.map``/``.forEach`` callback,
    or None if the key isn't inside one / the callback has no index parameter."""
    node = attr.node.parent
    while node is not None:
        if node.type in ("arrow_function", "function_expression"):
            args = node.parent
            if (
                args is not None
                and args.type == "arguments"
                and args.parent is not None
                and args.parent.type == "call_expression"
                and callee(Tsx(args.parent)) in _ITER_METHODS
            ):
                item, index = _param_names(Tsx(node))
                return (item, index) if index is not None else None
        node = node.parent
    return None


def _param_names(fn: Tsx) -> tuple[str | None, str | None]:
    params = fn.field("parameters")
    names = [_identifier_name(c) for c in params.named_children()] if params else []
    item = names[0] if len(names) >= 1 else None
    index = names[1] if len(names) >= 2 else None
    return item, index


def _identifier_name(param: Tsx) -> str | None:
    if param.type == "identifier":
        return param.text
    pattern = param.field("pattern")
    return (
        pattern.text if pattern is not None and pattern.type == "identifier" else None
    )


class MultiComponentFile(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-MULTI-COMPONENT-FILE"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 18

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        components = _top_level_components(ctx.root)
        if len(components) <= 1:
            return []
        if _is_compound_family(components):
            return []  # exported <Tabs>/<TabsList>/… family — a cohesive public API, not drift
        names = ", ".join(name for name, _, _ in components)
        return [
            self.make_finding(
                ctx,
                line=line,
                message=f"{len(components)} components in one file ({names}); one per file",
                suggestion="split each component into its own file (a provider wrapper may stay)",
            )
            for _, line, _ in components[1:]
        ]


class RepeatedJsx(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-REPEATED-JSX"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 13

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        threshold = ctx.config.effective(self.rule_id).threshold
        min_tags = threshold.jsx.repeated_jsx_min_tags
        min_repeat = threshold.jsx.repeated_jsx_min
        out: list[Finding] = []
        for parent in ctx.root.descendants("jsx_element"):
            by_shape: dict[tuple[str, ...], list[Tsx]] = defaultdict(list)
            for child in parent.child_elements():
                shape = _element_skeleton(child)
                if len(shape) >= min_tags:
                    by_shape[shape].append(child)
            for shape, members in by_shape.items():
                if len(members) >= min_repeat:
                    out.append(
                        self.make_finding(
                            ctx,
                            line=members[0].line,
                            message=f"{len(members)} sibling <{shape[0]}> blocks repeat the same shape; map over data",
                            suggestion="extract a typed config array and `.map()` it (or a small component)",
                        )
                    )
        return out


def _element_skeleton(element: Tsx) -> tuple[str, ...]:
    return tuple(n.jsx_name() for n in element.walk() if n.is_jsx_element)


def _top_level_components(root: Tsx) -> list[tuple[str, int, bool]]:
    """Top-level Capitalized declarations whose body renders JSX (name, line, exported)."""
    return [
        (name, at.line, exported)
        for name, body, at, exported in root.top_declarations()
        if _is_component(name, body)
    ]


def _is_compound_family(components: list[tuple[str, int, bool]]) -> bool:
    """A compound-component module (``Tabs``+``TabsList``+``TabsTrigger`` / ``Select``+
    ``SelectTrigger``+``SelectContent``): every part is exported and all share a PascalCase
    name prefix. A private sub-component (``Dashboard`` + unexported ``DashboardFooter``) is
    NOT this — it's the drift we flag."""
    if not all(exported for _, _, exported in components):
        return False
    names = [name for name, _, _ in components]
    prefix = _common_prefix(names)
    return len(prefix) >= 3 and prefix[0].isupper()


def _common_prefix(names: list[str]) -> str:
    lo, hi = min(names), max(names)
    end = 0
    while end < len(lo) and lo[end] == hi[end]:
        end += 1
    return lo[:end]


def _is_component(name: str, body: Tsx) -> bool:
    return is_pascal_case(name) and body.contains_jsx()


# --- TS-REACT-ASYNC-EFFECT ---------------------------------------------------------------

_EFFECT_HOOKS = {"useEffect", "useLayoutEffect"}


def _first_arg(call: Tsx) -> Tsx | None:
    args = call.field("arguments")
    children = args.named_children() if args is not None else []
    return children[0] if children else None


def _is_async_fn(node: Tsx) -> bool:
    """An arrow/function expression carrying the leading ``async`` token."""
    return node.type in ("arrow_function", "function_expression") and any(
        c.type == "async" for c in node.node.children
    )


class AsyncEffect(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-ASYNC-EFFECT"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.AUTO

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for call in ctx.root.descendants("call_expression"):
            if callee(call) not in _EFFECT_HOOKS:
                continue
            cb = _first_arg(call)
            if cb is not None and _is_async_fn(cb):
                out.append(
                    self.make_finding(
                        ctx,
                        line=call.line,
                        message="async function passed to useEffect — its returned Promise is treated as the cleanup, so cleanup never runs",
                        suggestion="keep the effect sync; define an async fn inside and call it, returning a real cleanup",
                    )
                )
        return out


# --- TS-REACT-RANDOM-KEY -----------------------------------------------------------------

#: (object, method) pairs that yield a fresh value on every call — fatal as a React key
_FRESH_KEYGEN = {("Math", "random"), ("Date", "now"), ("crypto", "randomUUID")}


def _member_pair(call: Tsx) -> tuple[str, str] | None:
    """(object, property) of a ``a.b(...)`` call, else None."""
    if call.type != "call_expression":
        return None
    fn = call.field("function")
    if fn is None or fn.type != "member_expression":
        return None
    obj, prop = fn.field("object"), fn.field("property")
    return (obj.text, prop.text) if obj is not None and prop is not None else None


class RandomKey(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-RANDOM-KEY"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.AUTO

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for attr in ctx.root.descendants("jsx_attribute"):
            if attr.attr_name() != "key":
                continue
            value = attr.attr_value()
            if value is not None and any(
                _member_pair(n) in _FRESH_KEYGEN for n in value.walk()
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=attr.line,
                        message="key is a freshly generated value (Math.random/Date.now/randomUUID) — a new key every render remounts the node and drops its state",
                        suggestion="key off a stable unique id from the data, not a per-render value",
                    )
                )
        return out


# --- TS-REACT-EAGER-STATE-INIT -----------------------------------------------------------

_EXPENSIVE_INIT = {
    ("JSON", "parse"),
    ("localStorage", "getItem"),
    ("sessionStorage", "getItem"),
}
_ARRAY_OPS = {"map", "filter", "reduce", "sort", "flatMap"}


def _is_expensive_init(node: Tsx) -> bool:
    """A useState argument that does real work on every render: JSON.parse(...),
    (local|session)Storage.getItem(...), or an array-transform chain."""
    pair = _member_pair(node)
    if pair is None:
        return False
    return pair in _EXPENSIVE_INIT or pair[1] in _ARRAY_OPS


class EagerStateInit(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-EAGER-STATE-INIT"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for call in ctx.root.descendants("call_expression"):
            if callee(call) != "useState":
                continue
            arg = _first_arg(call)
            if arg is not None and _is_expensive_init(arg):
                out.append(
                    self.make_finding(
                        ctx,
                        line=call.line,
                        message="useState initial value is computed by a call that re-runs every render — only the first render's value is kept",
                        suggestion="pass an initializer function (`useState(() => …)`) so the work runs once",
                    )
                )
        return out
