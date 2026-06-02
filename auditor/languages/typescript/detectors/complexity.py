"""Size & complexity detectors — objective, threshold-driven, project-agnostic. Thresholds
come from config (``[tool.auditor.rules.<id>.threshold]``), so a repo tunes its own bar.
"""

from typing import ClassVar

from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.nodes import Tsx, is_pascal_case
from auditor.models import Category, Finding, Severity, VerdictKind


class FileSize(TsDetector):
    rule_id: ClassVar[str] = "TS-STYLE-FILE-SIZE"
    category: ClassVar[Category] = Category.STYLE
    default_severity: ClassVar[Severity] = Severity.LOW
    checklist_item: ClassVar[int] = 18

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        limit = ctx.config.effective(self.rule_id).threshold.file_max_lines
        lines = len(ctx.lines)
        if lines <= limit:
            return []
        return [
            self.make_finding(
                ctx,
                line=1,
                message=f"file is {lines} lines (> {limit}); split it",
                evidence=f"{lines} lines",
                suggestion="split into smaller components/modules",
            )
        ]


class TooManyProps(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-TOO-MANY-PROPS"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 21

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        limit = ctx.config.effective(self.rule_id).threshold.max_params
        out: list[Finding] = []
        for name, body, at in _components(ctx.root):
            count = _prop_count(body)
            if count > limit:
                out.append(
                    self.make_finding(
                        ctx,
                        line=at.line,
                        message=f"{name} takes {count} props (> {limit}); group related ones into an object",
                        suggestion="bundle related props into a sub-object or split the component",
                    )
                )
        return out


class DeepJsxNesting(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-DEEP-JSX-NESTING"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 11

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        limit = ctx.config.effective(self.rule_id).threshold.max_jsx_depth
        out: list[Finding] = []
        for element in ctx.root.descendants("jsx_element", "jsx_self_closing_element"):
            if _jsx_depth(element) > limit:
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message=f"<{element.jsx_name()}> nests JSX > {limit} levels deep; extract a sub-component",
                        suggestion="lift inner blocks into their own components",
                    )
                )
                break  # one finding per file is enough to make the point
        return out


def _components(root: Tsx) -> list[tuple[str, Tsx, Tsx]]:
    return [
        (name, body, at)
        for name, body, at, _ in root.top_declarations()
        if is_pascal_case(name) and body.contains_jsx()
    ]


def _prop_count(body: Tsx) -> int:
    """Number of declared props: destructured names in the first param, else type members."""
    params = body.field("parameters")
    if params is None:
        return 0
    first = next(iter(params.named_children()), None)
    if first is None:
        return 0
    pattern = first.field("pattern") or first
    if pattern.type == "object_pattern":
        return sum(
            1
            for c in pattern.named_children()
            if c.type
            in (
                "shorthand_property_identifier_pattern",
                "pair_pattern",
                "object_assignment_pattern",
            )
        )
    annotation = first.field("type")
    if annotation is not None:
        return sum(1 for n in annotation.walk() if n.type == "property_signature")
    return 0


def _jsx_depth(element: Tsx) -> int:
    children = element.child_elements()
    if not children:
        return 1
    return 1 + max(_jsx_depth(c) for c in children)
