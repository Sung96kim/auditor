"""Accessibility detectors — objective, framework-agnostic, no design-system knowledge.

Every rule here is a structural WCAG-grounded fact that holds on any React codebase
regardless of styling system or primitive library: a non-interactive element wired as a
button, an icon-only control with no accessible name, an image with no alt text, a positive
tabindex. These need no project context, which is exactly why they belong in the mechanical
auditor rather than the judgment layer.
"""

from typing import ClassVar

from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.nodes import Tsx
from auditor.models import Category, Finding, Severity, VerdictKind

_ALL_ELEMENTS = ("jsx_element", "jsx_self_closing_element")
_LABEL_ATTRS = {"aria-label", "aria-labelledby", "title"}
_ROLE_ATTRS = {"role", "tabIndex", "tabindex"}
# raw html elements that are natively interactive (keyboard + focus) — onClick is fine on them
_NATIVELY_INTERACTIVE = {
    "a",
    "button",
    "input",
    "select",
    "textarea",
    "option",
    "label",
    "summary",
    "details",
}


class A11yDetector(TsDetector):
    abstract: ClassVar[bool] = True
    category: ClassVar[Category] = Category.A11Y
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 23


def _is_raw_html(name: str) -> bool:
    return bool(name) and name[0].islower() and "." not in name


def _is_button_like(name: str) -> bool:
    return name == "button" or "button" in name.lower()


def _is_icon(element: Tsx) -> bool:
    name = element.jsx_name()
    return name == "svg" or name.endswith("Icon") or name.endswith("Svg")


class NonInteractiveOnClick(A11yDetector):
    rule_id: ClassVar[str] = "TS-A11Y-NONINTERACTIVE-ONCLICK"

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            name = element.jsx_name()
            if not _is_raw_html(name) or name in _NATIVELY_INTERACTIVE:
                continue
            attrs = element.attributes()
            if "onClick" in attrs and not (_ROLE_ATTRS & attrs.keys()):
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message=f"<{name} onClick> acts as a control without keyboard/role support",
                        suggestion="use a real <button>/<a>, or add role, tabIndex, and onKeyDown",
                    )
                )
        return out


class IconButtonNoLabel(A11yDetector):
    rule_id: ClassVar[str] = "TS-A11Y-ICON-BUTTON-NO-LABEL"

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants("jsx_element"):
            if not _is_button_like(element.jsx_name()):
                continue
            attrs = element.attributes()
            children = element.child_elements()
            icon_only = (
                not element.has_text_child()
                and len(children) == 1
                and _is_icon(children[0])
            )
            if icon_only and not (_LABEL_ATTRS & attrs.keys()):
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message="icon-only button has no accessible name",
                        suggestion="add aria-label (or a wrapping tooltip with a label)",
                    )
                )
        return out


class ImgNoAlt(A11yDetector):
    rule_id: ClassVar[str] = "TS-A11Y-IMG-NO-ALT"

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            if element.jsx_name() != "img":
                continue
            attrs = element.attributes()
            if "alt" not in attrs and "aria-hidden" not in attrs:
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message="<img> has no alt attribute",
                        suggestion='add alt="…" (or alt="" / aria-hidden for decorative images)',
                    )
                )
        return out


class PositiveTabIndex(A11yDetector):
    rule_id: ClassVar[str] = "TS-A11Y-POSITIVE-TABINDEX"

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            attrs = element.attributes()
            attr = attrs.get("tabIndex") or attrs.get("tabindex")
            if attr is None:
                continue
            value = attr.attr_value_text().strip()
            if value.lstrip("-").isdigit() and int(value) > 0:
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message=f"positive tabIndex ({value}) overrides natural tab order",
                        suggestion="use tabIndex={0} (focusable) or -1 (programmatic), not a positive value",
                    )
                )
        return out
