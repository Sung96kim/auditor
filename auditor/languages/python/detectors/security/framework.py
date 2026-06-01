"""Framework & runtime-hardening security detectors."""

import ast
import re
from typing import ClassVar

from auditor.languages.base import AuditContext
from auditor.languages.python.detectors._util import call_attr, dotted_name, kwarg
from auditor.languages.python.detectors.security._base import (
    SecurityDetector,
    has_true_kwarg,
    string_constants,
)
from auditor.models import Finding, Severity, VerdictKind


class FlaskDebug(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-FLASK-DEBUG"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B201", "owasp:A05")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call) and call_attr(node) == "run" and has_true_kwarg(node, "debug"):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="`run(debug=True)` exposes the Werkzeug debugger (RCE) in production",
                        suggestion="drive debug from config; never True in production",
                    )
                )
        return out


class JinjaAutoescapeOff(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-JINJA-AUTOESCAPE-OFF"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B701", "owasp:A03")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call) and call_attr(node) == "Environment":
                autoescape = kwarg(node, "autoescape")
                if autoescape is None or (isinstance(autoescape, ast.Constant) and autoescape.value is False):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message="Jinja Environment without autoescape=True (XSS risk)",
                            suggestion="pass autoescape=True (or select_autoescape(...))",
                        )
                    )
        return out


_SEC_ASSERT = re.compile(r"(auth|admin|permission|allowed|is_owner|authorized|can_)", re.I)


class AssertForSecurity(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-ASSERT-FOR-SECURITY"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B101", "owasp:A04")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Assert):
                names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
                attrs = {n.attr for n in ast.walk(node.test) if isinstance(n, ast.Attribute)}
                if any(_SEC_ASSERT.search(x) for x in names | attrs):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message="security check via `assert` (stripped under python -O)",
                            suggestion="raise an explicit exception instead of asserting",
                        )
                    )
        return out


class InsecureTempfile(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-INSECURE-TEMPFILE"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B306", "bandit:B108", "owasp:A05")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call) and dotted_name(node.func) == "tempfile.mktemp":
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="tempfile.mktemp() is race-prone; use mkstemp()",
                        suggestion="use tempfile.mkstemp / NamedTemporaryFile",
                    )
                )
        for node in string_constants(ctx.tree):
            if node.value.startswith(("/tmp/", "/var/tmp/")) or node.value in ("/tmp", "/var/tmp"):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"hardcoded temp path `{node.value}`; predictable/insecure",
                        suggestion="use tempfile for temp paths",
                    )
                )
        return out
