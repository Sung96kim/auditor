"""Design-system detectors — opt-in, driven entirely by the project's declared design system
(``[tool.auditor.design_system]``). The auditor hardcodes no component vocabulary; the repo
supplies it (its shell path, its primitives + the raw markup each replaces), and these rules
check against *that*. With no design system declared they do nothing.

This is the resolution to the agnostic-vs-design-system tension: the mechanical default stays
project-agnostic, and a team that wants 'this should be <Badge>' / 'use the shell' / 'use the
size prop' enforcement opts in by declaring its system.
"""

import re
from typing import TYPE_CHECKING, ClassVar

from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.nodes import Tsx, import_source
from auditor.models import Category, Finding, Severity, VerdictKind

if TYPE_CHECKING:
    from auditor.config import DesignSystemPrimitive

_Rule = tuple["DesignSystemPrimitive", "re.Pattern[str]"]

_ALL_ELEMENTS = ("jsx_element", "jsx_self_closing_element")
_SIZE_CLASS = re.compile(r"\bh-\d|\bw-\d|\bsize-\d")
_ALIAS_ROOTS = ("@", "~", "#", "$")


def _segments(path: str) -> list[str]:
    return [s for s in path.replace("\\", "/").split("/") if s]


def _alias_stripped(ui_path: str) -> list[str]:
    """The directory segments of an import alias with a leading alias root dropped — the part
    that maps onto a real file path. ``@/components/ui`` -> ``[components, ui]``. The root
    (``@``, ``~``, ``@scope``, ``src`` aliases vary per project) is whatever precedes it."""
    parts = _segments(ui_path)
    if parts and (parts[0] == "." or parts[0][0] in _ALIAS_ROOTS):
        parts = parts[1:]
    return parts


def _under_ui_layer(file_path: str, ui_paths: list[str]) -> bool:
    """Match a configured ui path against the real file path by whole trailing segments, so
    it works regardless of the project's alias root (start from the back, not the prefix)."""
    haystack = _segments(file_path)
    for ui_path in ui_paths:
        needle = _alias_stripped(ui_path)
        n = len(needle)
        if n and any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1)):
            return True
    return False


class _DesignSystemDetector(TsDetector):
    abstract: ClassVar[bool] = True
    category: ClassVar[Category] = Category.DESIGN_SYSTEM
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE


class DirectUiImport(_DesignSystemDetector):
    rule_id: ClassVar[str] = "TS-DS-DIRECT-UI-IMPORT"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    checklist_item: ClassVar[int] = 17

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        ds = ctx.config.settings.design_system
        if not ds.ui_paths:
            return []  # no design system declared — rule is dormant
        if _under_ui_layer(ctx.file_path, ds.ui_paths):
            return []  # the ui layer itself wraps the raw primitives — don't flag it
        shell = f" (import from {ds.shell})" if ds.shell else ""
        out: list[Finding] = []
        for node in ctx.root.named_children():
            if node.type != "import_statement":
                continue
            src = import_source(node)
            if src and any(path in src for path in ds.ui_paths):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.line,
                        message=f"direct ui import from '{src}'; go through the design-system shell{shell}",
                        suggestion="import the wrapped primitive from the shell, not the raw ui layer",
                    )
                )
        return out


class InlinePrimitive(_DesignSystemDetector):
    rule_id: ClassVar[str] = "TS-DS-INLINE-PRIMITIVE"
    checklist_item: ClassVar[int] = 7

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        rules = [
            (p, re.compile(p.when_class))
            for p in ctx.config.settings.design_system.primitives
            if p.when_class
        ]
        if not rules:
            return []
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            primitive = self._matched_primitive(element, rules)
            if primitive is not None:
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message=f"inline markup matches your <{primitive}>; use it instead",
                        suggestion=f"replace the raw markup with <{primitive}>",
                    )
                )
        return out

    @staticmethod
    def _matched_primitive(element: Tsx, rules: list[_Rule]) -> str | None:
        """The declared primitive whose pattern this element's className matches, else None."""
        class_attr = element.attributes().get("className")
        if class_attr is None:
            return None
        classes = class_attr.attr_value_text()
        name = element.jsx_name()
        for primitive, pattern in rules:
            if name == primitive.component or not pattern.search(classes):
                continue
            if primitive.requires_text and not element.has_text_child():
                continue  # icon-only backdrop, not the primitive
            return primitive.component
        return None


class SizeOverride(_DesignSystemDetector):
    rule_id: ClassVar[str] = "TS-DS-SIZE-OVERRIDE"
    checklist_item: ClassVar[int] = 16

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        sized = {
            p.component
            for p in ctx.config.settings.design_system.primitives
            if p.size_override
        }
        if not sized:
            return []
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            if element.jsx_name() not in sized:
                continue
            class_attr = element.attributes().get("className")
            if class_attr is not None and _SIZE_CLASS.search(class_attr.attr_value_text()):
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message=f"<{element.jsx_name()}> sized via className; use its size prop",
                        suggestion="use the component's size prop (add the size to the DS if missing)",
                    )
                )
        return out
