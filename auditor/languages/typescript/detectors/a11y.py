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
_FORM_CONTROLS = {"input", "select", "textarea"}
_UNLABELLED_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}
_NAMING_ATTRS = {"aria-label", "aria-labelledby", "id", "title"}
_MOUSE_ATTRS = {"onMouseOver", "onMouseOut"}
_FOCUS_ATTRS = {"onFocus", "onBlur"}
_IMPLICIT_ROLE = {
    "a": "link",
    "button": "button",
    "nav": "navigation",
    "ul": "list",
    "ol": "list",
    "li": "listitem",
    "img": "img",
    "table": "table",
    "main": "main",
    "header": "banner",
    "footer": "contentinfo",
    "article": "article",
    "form": "form",
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


class FormControlNoLabel(A11yDetector):
    rule_id: ClassVar[str] = "TS-A11Y-FORM-LABEL"

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            name = element.jsx_name()
            if name not in _FORM_CONTROLS:
                continue
            attrs = element.attributes()
            if name == "input":
                input_type = attrs["type"].attr_value_text() if "type" in attrs else "text"
                if input_type in _UNLABELLED_INPUT_TYPES:
                    continue
            if not (_NAMING_ATTRS & attrs.keys()):
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message=f"<{name}> has no associated label (aria-label / id+htmlFor)",
                        suggestion="add aria-label, or an id linked from a <label htmlFor>",
                    )
                )
        return out


class AnchorNoHref(A11yDetector):
    rule_id: ClassVar[str] = "TS-A11Y-ANCHOR-NO-HREF"

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            if element.jsx_name() != "a":
                continue
            if "href" not in element.attributes():
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message="<a> without href is not a real link/keyboard-focusable",
                        suggestion="add href, or use a <button> if it triggers an action",
                    )
                )
        return out


class AutoFocus(A11yDetector):
    rule_id: ClassVar[str] = "TS-A11Y-AUTOFOCUS"
    default_severity: ClassVar[Severity] = Severity.LOW

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            if "autoFocus" in element.attributes():
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message="autoFocus can disorient screen-reader and keyboard users",
                        suggestion="move focus intentionally in response to a user action instead",
                    )
                )
        return out


class RedundantRole(A11yDetector):
    rule_id: ClassVar[str] = "TS-A11Y-REDUNDANT-ROLE"
    default_severity: ClassVar[Severity] = Severity.LOW

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            name = element.jsx_name()
            role = element.attributes().get("role")
            if role is not None and role.attr_value_text() == _IMPLICIT_ROLE.get(name):
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message=f'role="{_IMPLICIT_ROLE[name]}" is redundant on <{name}>',
                        suggestion="drop the redundant role (it's the element's implicit role)",
                    )
                )
        return out


class MouseOnlyHandler(A11yDetector):
    rule_id: ClassVar[str] = "TS-A11Y-MOUSE-NO-KEY"

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            attrs = element.attributes()
            if (_MOUSE_ATTRS & attrs.keys()) and not (_FOCUS_ATTRS & attrs.keys()):
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message="onMouseOver/onMouseOut without onFocus/onBlur; keyboard users miss it",
                        suggestion="pair mouse handlers with onFocus/onBlur",
                    )
                )
        return out


class IframeNoTitle(A11yDetector):
    rule_id: ClassVar[str] = "TS-A11Y-IFRAME-TITLE"

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            if element.jsx_name() == "iframe" and "title" not in element.attributes():
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message="<iframe> has no title (screen readers can't describe it)",
                        suggestion='add a descriptive title="…"',
                    )
                )
        return out
