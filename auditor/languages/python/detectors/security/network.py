"""Transport & network security detectors."""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext
from auditor.languages.python.detectors._util import dotted_name, kwarg
from auditor.languages.python.detectors.security._base import (
    SecurityDetector,
    has_false_kwarg,
    string_constants,
)
from auditor.models import Finding, Severity, VerdictKind

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "request"}
_HTTP_LIBS = ("requests.", "httpx.")


def _is_http_call(node: ast.Call) -> str | None:
    name = dotted_name(node.func)
    if name.startswith(_HTTP_LIBS) and name.rsplit(".", 1)[-1] in _HTTP_METHODS:
        return name
    return None


class InsecureTls(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-INSECURE-TLS"
    default_severity: ClassVar[Severity] = Severity.HIGH
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B501", "owasp:A02")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call):
                name = dotted_name(node.func)
                if has_false_kwarg(node, "verify"):
                    out.append(self._f(ctx, node, "TLS verification disabled (verify=False)"))
                elif has_false_kwarg(node, "check_hostname"):
                    out.append(self._f(ctx, node, "hostname check disabled (check_hostname=False)"))
                elif name == "ssl._create_unverified_context":
                    out.append(self._f(ctx, node, "ssl._create_unverified_context() disables verification"))
        return out

    def _f(self, ctx: AuditContext, node: ast.Call, msg: str) -> Finding:
        return self.make_finding(ctx, line=node.lineno, message=msg, suggestion="keep TLS verification on")


class RequestNoTimeout(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-REQUEST-NO-TIMEOUT"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B113", "owasp:A06")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call):
                name = _is_http_call(node)
                if name and kwarg(node, "timeout") is None:
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message=f"`{name}(...)` has no timeout; can hang indefinitely",
                            suggestion="pass timeout=<seconds>",
                        )
                    )
        return out


class BindAllInterfaces(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-BIND-ALL-INTERFACES"
    default_severity: ClassVar[Severity] = Severity.LOW
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B104", "owasp:A05")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in string_constants(ctx.tree):
            if node.value == "0.0.0.0":
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="binds all interfaces (0.0.0.0); exposes the service broadly",
                        suggestion="bind to a specific interface unless intentionally public",
                    )
                )
        return out


class ParamikoAutoadd(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-PARAMIKO-AUTOADD"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B507", "owasp:A07")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call):
                name = dotted_name(node.func)
                if name.endswith("AutoAddPolicy") or name.endswith("WarningPolicy"):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message=f"`{name}` accepts unknown SSH host keys (MITM risk)",
                            suggestion="use RejectPolicy and pin known host keys",
                        )
                    )
        return out


class Ssrf(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-SSRF"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    standard_refs: ClassVar[tuple[str, ...]] = ("owasp:A10",)

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call) and _is_http_call(node):
                url = node.args[0] if node.args else kwarg(node, "url")
                if url is not None and not isinstance(url, ast.Constant):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message="outbound HTTP to a non-constant URL; possible SSRF",
                            suggestion="validate/allow-list the destination host",
                        )
                    )
        return out
