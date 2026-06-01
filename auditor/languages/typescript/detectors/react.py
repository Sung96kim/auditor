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

_MIN_REPEAT = 3  # 3+ identical-shape siblings is a strong "map over data" signal
_MIN_TAGS = 2  # ignore repeated leaf elements (<br/>, <li>text</li>) — too trivial
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
            index = _enclosing_iter_index(attr)
            if value is None or index is None:
                continue
            if index in {n.text for n in value.walk() if n.type == "identifier"}:
                out.append(
                    self.make_finding(
                        ctx,
                        line=attr.line,
                        message=f"`key={{{index}}}` uses the array index; unstable when the list reorders/inserts",
                        suggestion="key off a stable unique id from the item, not its position",
                    )
                )
        return out


def _enclosing_iter_index(attr: Tsx) -> str | None:
    """The index parameter of the nearest enclosing ``.map``/``.forEach`` callback, if any."""
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
                return _second_param_name(Tsx(node))
        node = node.parent
    return None


def _second_param_name(fn: Tsx) -> str | None:
    params = fn.field("parameters")
    if params is None:
        return None
    children = params.named_children()
    if len(children) < 2:
        return None
    second = children[1]
    if second.type == "identifier":
        return second.text
    pattern = second.field("pattern")
    return pattern.text if pattern is not None and pattern.type == "identifier" else None


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
        out: list[Finding] = []
        for parent in ctx.root.descendants("jsx_element"):
            by_shape: dict[tuple[str, ...], list[Tsx]] = defaultdict(list)
            for child in parent.child_elements():
                shape = _element_skeleton(child)
                if len(shape) >= _MIN_TAGS:
                    by_shape[shape].append(child)
            for shape, members in by_shape.items():
                if len(members) >= _MIN_REPEAT:
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
