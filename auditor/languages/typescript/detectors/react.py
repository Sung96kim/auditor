"""React structural detectors that need no design-system knowledge.

The auditor runs on any repo with no knowledge of its primitive vocabulary, so it does NOT
map raw markup to specific primitives ("this should be <Badge>") or enforce project styling
conventions — that requires reading the project's design system first, which is the agent +
frontend-design-system-review skill's job. What stays here is objective structure.
"""

from collections import defaultdict
from typing import ClassVar

from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.nodes import Tsx
from auditor.models import Category, Finding, Severity, VerdictKind

_MIN_REPEAT = 3  # 3+ identical-shape siblings is a strong "map over data" signal
_MIN_TAGS = 2  # ignore repeated leaf elements (<br/>, <li>text</li>) — too trivial


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
        names = ", ".join(name for name, _ in components)
        return [
            self.make_finding(
                ctx,
                line=line,
                message=f"{len(components)} components in one file ({names}); one per file",
                suggestion="split each component into its own file (a provider wrapper may stay)",
            )
            for _, line in components[1:]
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


def _top_level_components(root: Tsx) -> list[tuple[str, int]]:
    """Top-level Capitalized declarations whose body renders JSX (i.e. React components)."""
    return [
        (name, at.line)
        for name, body, at in root.top_declarations()
        if _is_component(name, body)
    ]


def _is_component(name: str, body: Tsx) -> bool:
    return bool(name) and name[0].isupper() and body.contains_jsx()
