"""DRY / extraction detectors — objective, project-agnostic structure.

These surface *mechanical* extraction signals; the agent maps them to the project's actual
hook/util/primitive (the auditor never names one):

- ``EXTRACTABLE-HOOK``: a component carrying a large cluster of hook calls → lift the stateful
  logic into a custom ``use*`` hook.
- ``EXTRACTABLE-HELPER``: a pure helper function nested in a component that closes over none of
  its state → lift to a module-level util.
- ``PARALLEL-SIBLING``: 2+ top-level functions/components with identical structure differing
  only in constants → unify into one parameterized definition (a prop/argument).
"""

from collections.abc import Iterable
from typing import ClassVar

from auditor.languages.base import ParallelSiblingMixin
from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.nodes import Tsx, callee, field_text, is_pascal_case
from auditor.models import Category, Finding, Severity, VerdictKind

_HOOKS = {
    "useState",
    "useEffect",
    "useLayoutEffect",
    "useMemo",
    "useCallback",
    "useReducer",
    "useRef",
    "useImperativeHandle",
    "useTransition",
}
_MIN_HOOKS = 5  # below this a component's state plumbing isn't worth a custom hook
# parallel-sibling only applies to things you'd *parameterize* — functions/components — not
# plain data consts (two different lookup maps aren't "one function with an argument").
_PARAMETERIZABLE = {"function_declaration", "arrow_function", "function_expression"}
_LITERALS = {"number", "string", "template_string", "true", "false", "regex", "null"}
_STRUCTURE = {
    "if_statement",
    "for_statement",
    "for_in_statement",
    "while_statement",
    "switch_statement",
    "return_statement",
    "ternary_expression",
    "try_statement",
}


def _is_component(name: str, body: Tsx) -> bool:
    return is_pascal_case(name) and body.contains_jsx()


class ExtractableHook(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-EXTRACTABLE-HOOK"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 14

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for name, body, at, _ in ctx.root.top_declarations():
            if not _is_component(name, body):
                continue
            hooks = sum(
                1
                for n in body.walk()
                if n.type == "call_expression" and callee(n) in _HOOKS
            )
            if hooks >= _MIN_HOOKS:
                out.append(
                    self.make_finding(
                        ctx,
                        line=at.line,
                        message=f"{name} calls {hooks} hooks; extract cohesive stateful logic into a custom use* hook",
                        suggestion="move related useState/useEffect clusters into a use<Behavior>() hook",
                    )
                )
        return out


class ExtractableHelper(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-EXTRACTABLE-HELPER"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 15

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for name, body, _, _ in ctx.root.top_declarations():
            if not _is_component(name, body):
                continue
            component_locals = self._bound_names(body)
            for helper in self._nested_functions(body):
                inner = self._bound_names(helper)
                free = self._used_names(helper) - inner
                if free and not (free & (component_locals - inner)):
                    hname = helper.field("name")
                    out.append(
                        self.make_finding(
                            ctx,
                            line=helper.line,
                            message=f"helper `{hname.text if hname else '?'}` uses no component state; lift to a module-level util",
                            suggestion="move it to lib/ or module scope and import it (reusable + testable)",
                        )
                    )
        return out

    @staticmethod
    def _nested_functions(body: Tsx) -> list[Tsx]:
        return [
            n
            for n in body.walk()
            if n.type == "function_declaration" and n.node != body.node
        ]

    @staticmethod
    def _bound_names(scope: Tsx) -> set[str]:
        """Names introduced (declared/parameter/destructured) anywhere in ``scope``."""
        binders = {
            "variable_declarator",
            "required_parameter",
            "optional_parameter",
            "object_pattern",
            "array_pattern",
            "function_declaration",
        }
        names: set[str] = set()
        for n in scope.walk():
            if n.type in ("identifier", "shorthand_property_identifier_pattern"):
                parent = n.node.parent
                if parent is not None and parent.type in binders:
                    names.add(n.text)
        return names

    @staticmethod
    def _used_names(scope: Tsx) -> set[str]:
        return {n.text for n in scope.walk() if n.type == "identifier"}


class ParallelSibling(ParallelSiblingMixin[TsAuditContext, Tsx], TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-PARALLEL-SIBLING"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 17

    def _candidates(self, ctx: TsAuditContext) -> list[tuple[str, int, Tsx]]:
        # skip data consts (lookup maps, style objects) — not parameterizable
        return [
            (name, at.line, body)
            for name, body, at, _ in ctx.root.top_declarations()
            if body.type in _PARAMETERIZABLE
        ]

    def _walk(self, root: Tsx) -> Iterable[Tsx]:
        return root.walk()

    def _min_skeleton(self, ctx: TsAuditContext) -> int:
        return ctx.config.effective(self.rule_id).threshold.parallel_sibling_min_tokens

    def _min_group(self, ctx: TsAuditContext) -> int:
        return ctx.config.effective(self.rule_id).threshold.parallel_sibling_min_group

    def _token(self, node: Tsx) -> tuple[str | None, str | None]:
        """One structural token for ``node``, plus its literal text if it is a constant.
        Property/member names are kept so twins that differ in *which* function/option they
        reference (not just a constant) are NOT treated as parameterizable siblings."""
        t = node.type
        if t in _LITERALS:
            return "L", node.text
        if t == "jsx_text":
            stripped = node.text.strip()
            return ("L", stripped) if stripped else (None, None)
        if t == "call_expression":
            return "c:" + callee(node), None
        if t == "member_expression":
            return "m:" + field_text(node, "property"), None
        if t == "pair":
            return "k:" + field_text(node, "key"), None
        if t in ("jsx_opening_element", "jsx_self_closing_element"):
            return "j:" + field_text(node, "name"), None
        if t in _STRUCTURE:
            return t, None
        return None, None
