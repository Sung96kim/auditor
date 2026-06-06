"""Frontend security detectors — objective, OWASP-mapped, project-agnostic.

XSS via ``dangerouslySetInnerHTML`` / ``javascript:`` URLs / ``eval``, and reverse-tabnabbing
via ``target="_blank"`` without ``rel="noopener"``. All hold on any React codebase.
"""

from typing import ClassVar

from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.nodes import Tsx, callee, field_text
from auditor.models import Category, Finding, Severity, VerdictKind

_ALL_ELEMENTS = ("jsx_element", "jsx_self_closing_element")
_URL_ATTRS = {"href", "src", "to", "action", "formAction"}


class DangerousHtml(TsDetector):
    rule_id: ClassVar[str] = "TS-SEC-DANGEROUS-HTML"
    category: ClassVar[Category] = Category.SECURITY
    default_severity: ClassVar[Severity] = Severity.HIGH
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    standard_refs: ClassVar[tuple[str, ...]] = ("owasp:A03",)

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            attr = element.attributes().get("dangerouslySetInnerHTML")
            if attr is None:
                continue
            html = _inner_html_value(attr)
            if html is not None and html.type != "string":
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message="dangerouslySetInnerHTML with non-constant HTML risks XSS",
                        suggestion="render text/JSX, or sanitize (DOMPurify) before setting __html",
                    )
                )
        return out


class TargetBlankNoopener(TsDetector):
    rule_id: ClassVar[str] = "TS-SEC-TARGET-BLANK-NOOPENER"
    category: ClassVar[Category] = Category.SECURITY
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    standard_refs: ClassVar[tuple[str, ...]] = ("owasp:A05",)

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            attrs = element.attributes()
            target = attrs.get("target")
            if target is None or target.attr_value_text() != "_blank":
                continue
            rel = attrs["rel"].attr_value_text() if "rel" in attrs else ""
            if "noopener" not in rel and "noreferrer" not in rel:
                out.append(
                    self.make_finding(
                        ctx,
                        line=element.line,
                        message='target="_blank" without rel="noopener" enables reverse tabnabbing',
                        suggestion='add rel="noopener noreferrer"',
                    )
                )
        return out


class JavascriptUrl(TsDetector):
    rule_id: ClassVar[str] = "TS-SEC-JAVASCRIPT-URL"
    category: ClassVar[Category] = Category.SECURITY
    default_severity: ClassVar[Severity] = Severity.HIGH
    standard_refs: ClassVar[tuple[str, ...]] = ("owasp:A03",)

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for element in ctx.root.descendants(*_ALL_ELEMENTS):
            for name, attr in element.attributes().items():
                if (
                    name in _URL_ATTRS
                    and attr.attr_value_text().strip().lower().startswith("javascript:")
                ):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=element.line,
                            message=f"`{name}` uses a javascript: URL (script injection)",
                            suggestion="use a real URL or an onClick handler, not javascript:",
                        )
                    )
        return out


class DangerousEval(TsDetector):
    rule_id: ClassVar[str] = "TS-SEC-DANGEROUS-EVAL"
    category: ClassVar[Category] = Category.SECURITY
    default_severity: ClassVar[Severity] = Severity.HIGH
    standard_refs: ClassVar[tuple[str, ...]] = ("owasp:A03",)

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ctx.root.descendants("call_expression", "new_expression"):
            name = (
                callee(node)
                if node.type == "call_expression"
                else field_text(node, "constructor")
            )
            if name == "eval" or (name == "Function" and node.type == "new_expression"):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.line,
                        message=f"`{name}` executes arbitrary code (injection risk)",
                        suggestion="parse/dispatch explicitly instead of eval/new Function",
                    )
                )
            elif (
                node.type == "call_expression"
                and name in ("setTimeout", "setInterval")
                and _first_arg_is_string(node)
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.line,
                        message=f"`{name}` with a string argument evaluates it as code (injection risk)",
                        suggestion="pass a function, not a string, to setTimeout/setInterval",
                    )
                )
        return out


def _first_arg_is_string(call: Tsx) -> bool:
    """True when the call's first argument is a string/template literal — the eval-equivalent
    form of ``setTimeout``/``setInterval``. A function or identifier first arg is the safe form."""
    args = call.field("arguments")
    if args is None:
        return False
    named = args.named_children()
    return bool(named) and named[0].type in ("string", "template_string")


def _inner_html_value(attr: Tsx) -> Tsx | None:
    """The ``__html`` value node inside ``dangerouslySetInnerHTML={{ __html: X }}``."""
    value = attr.attr_value()
    if value is None or value.type != "jsx_expression":
        return None
    for child in value.named_children():
        if child.type == "object":
            for pair in child.named_children():
                if pair.type == "pair":
                    key = pair.field("key")
                    if key is not None and key.text == "__html":
                        return pair.field("value")
    return None
