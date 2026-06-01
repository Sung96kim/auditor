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

from collections import defaultdict
from typing import ClassVar

from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.nodes import Tsx
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
_MIN_SKELETON = 4  # parallel-sibling needs real structural substance to be a true twin
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


def _callee(call: Tsx) -> str:
    fn = call.field("function")
    if fn is None:
        return ""
    if fn.type == "member_expression":
        prop = fn.field("property")
        return prop.text if prop is not None else ""
    if fn.type == "identifier":
        return fn.text
    return ""


def _top_decls(root: Tsx) -> list[tuple[str, Tsx, Tsx]]:
    """(name, body, anchor) for each top-level function and arrow-const."""
    out: list[tuple[str, Tsx, Tsx]] = []
    for top in root.named_children():
        decl = top.unwrap_export()
        if decl.type == "function_declaration":
            name = decl.field("name")
            if name is not None:
                out.append((name.text, decl, decl))
        elif decl.type == "lexical_declaration":
            for d in decl.named_children():
                if d.type != "variable_declarator":
                    continue
                name, value = d.field("name"), d.field("value")
                if name is not None and value is not None and value.type in (
                    "arrow_function",
                    "function_expression",
                ):
                    out.append((name.text, value, d))
    return out


def _is_component(name: str, body: Tsx) -> bool:
    return bool(name) and name[0].isupper() and body.contains_jsx()


class ExtractableHook(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-EXTRACTABLE-HOOK"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 14

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        out: list[Finding] = []
        for name, body, at in _top_decls(ctx.root):
            if not _is_component(name, body):
                continue
            hooks = sum(
                1
                for n in body.walk()
                if n.type == "call_expression" and _callee(n) in _HOOKS
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
        for name, body, _ in _top_decls(ctx.root):
            if not _is_component(name, body):
                continue
            component_locals = _bound_names(body)
            for helper in _nested_function_decls(body):
                inner = _bound_names(helper)
                free = _used_names(helper) - inner
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


class ParallelSibling(TsDetector):
    rule_id: ClassVar[str] = "TS-REACT-PARALLEL-SIBLING"
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 17

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        groups: dict[tuple[str, ...], list[tuple[str, int, tuple[str, ...]]]] = defaultdict(list)
        for name, body, at in _top_decls(ctx.root):
            skeleton, literals = _skeleton_literals(body)
            if len(skeleton) >= _MIN_SKELETON:
                groups[skeleton].append((name, at.line, literals))

        out: list[Finding] = []
        for members in groups.values():
            distinct_literals = {m[2] for m in members}
            if len(members) < 2 or len(distinct_literals) < 2:
                continue  # need ≥2 twins that actually differ in their constants
            names = ", ".join(m[0] for m in members)
            for name, line, _ in members:
                out.append(
                    self.make_finding(
                        ctx,
                        line=line,
                        message=f"{name} is a near-twin of {names} (same structure, different constants)",
                        suggestion="unify into one definition parameterized by the differing value (a prop/arg)",
                    )
                )
        return out


def _nested_function_decls(body: Tsx) -> list[Tsx]:
    return [n for n in body.walk() if n.type == "function_declaration" and n.node != body.node]


def _bound_names(scope: Tsx) -> set[str]:
    names: set[str] = set()
    for n in scope.walk():
        if n.type in ("identifier", "shorthand_property_identifier_pattern"):
            parent = n.node.parent
            if parent is not None and parent.type in (
                "variable_declarator",
                "required_parameter",
                "optional_parameter",
                "object_pattern",
                "array_pattern",
                "function_declaration",
            ):
                names.add(n.text)
    return names


def _used_names(scope: Tsx) -> set[str]:
    return {n.text for n in scope.walk() if n.type == "identifier"}


def _skeleton_literals(body: Tsx) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A literal-blind structural skeleton + the sequence of actual constants. Two defs with
    the same skeleton but different literals are parallel siblings."""
    skeleton: list[str] = []
    literals: list[str] = []
    for n in body.walk():
        token, literal = _token(n)
        if token:
            skeleton.append(token)
            if literal:
                literals.append(literal)
    return tuple(skeleton), tuple(literals)


def _field_text(node: Tsx, field: str) -> str:
    child = node.field(field)
    return child.text if child is not None else ""


def _token(node: Tsx) -> tuple[str | None, str | None]:
    """One structural token for ``node``, plus its literal text if it is a constant. Property
    and member names are kept so twins that differ in *which* function/option they reference
    (not just a constant) are NOT treated as parameterizable siblings."""
    t = node.type
    if t in _LITERALS:
        return "L", node.text
    if t == "jsx_text":
        stripped = node.text.strip()
        return ("L", stripped) if stripped else (None, None)
    if t == "call_expression":
        return "c:" + _callee(node), None
    if t == "member_expression":
        return "m:" + _field_text(node, "property"), None
    if t == "pair":
        return "k:" + _field_text(node, "key"), None
    if t in ("jsx_opening_element", "jsx_self_closing_element"):
        return "j:" + _field_text(node, "name"), None
    if t in _STRUCTURE:
        return t, None
    return None, None
