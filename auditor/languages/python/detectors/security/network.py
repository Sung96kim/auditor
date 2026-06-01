"""Transport & network security detectors."""

import ast
import re
from typing import ClassVar

from auditor.languages.base import AuditContext
from auditor.languages.python.detectors._util import (
    call_attr,
    dotted_name,
    function_params,
    kwarg,
    nearest_enclosing_function,
)
from auditor.languages.python.detectors.security._base import (
    SecurityDetector,
    has_false_kwarg,
)
from auditor.models import Finding, Severity, VerdictKind

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "request"}
_HTTP_LIBS = ("requests.", "httpx.")

_ALL_IFACE = "0.0.0.0"
_HOST_KWARGS = {
    "host",
    "hostname",
    "bind",
    "address",
    "addr",
    "interface",
    "server_address",
    "listen",
}
_BIND_FUNCS = {
    "bind",
    "run",
    "serve",
    "create_server",
    "run_server",
    "make_server",
    "listen",
}
_HOST_NAME = re.compile(r"host|bind|addr|interface|listen", re.I)


def _is_all_iface(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == _ALL_IFACE


def _binds_all_interfaces(node: ast.AST) -> ast.Constant | None:
    """The ``"0.0.0.0"`` literal only when used as a bind address — a host kwarg, a
    bind/run/serve arg, or a host-like assignment. A bare literal elsewhere is not flagged."""
    if isinstance(node, ast.Call):
        for kw in node.keywords:
            if kw.arg in _HOST_KWARGS and _is_all_iface(kw.value):
                return kw.value
        if call_attr(node) in _BIND_FUNCS:
            for arg in node.args:
                if _is_all_iface(arg):
                    return arg
                if (
                    isinstance(arg, ast.Tuple)
                    and arg.elts
                    and _is_all_iface(arg.elts[0])
                ):
                    return arg.elts[0]
    elif isinstance(node, ast.Assign) and len(node.targets) == 1:
        tgt = node.targets[0]
        if (
            isinstance(tgt, ast.Name)
            and _HOST_NAME.search(tgt.id)
            and _is_all_iface(node.value)
        ):
            return node.value
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if _HOST_NAME.search(node.target.id) and _is_all_iface(node.value):
            return node.value
    return None


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
                    out.append(
                        self._f(ctx, node, "TLS verification disabled (verify=False)")
                    )
                elif has_false_kwarg(node, "check_hostname"):
                    out.append(
                        self._f(
                            ctx, node, "hostname check disabled (check_hostname=False)"
                        )
                    )
                elif name == "ssl._create_unverified_context":
                    out.append(
                        self._f(
                            ctx,
                            node,
                            "ssl._create_unverified_context() disables verification",
                        )
                    )
        return out

    def _f(self, ctx: AuditContext, node: ast.Call, msg: str) -> Finding:
        return self.make_finding(
            ctx, line=node.lineno, message=msg, suggestion="keep TLS verification on"
        )


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
        for node in ast.walk(ctx.tree):
            target = _binds_all_interfaces(node)
            if target is not None:
                out.append(
                    self.make_finding(
                        ctx,
                        line=target.lineno,
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


def _url_is_user_derived(url: ast.expr, params: set[str]) -> bool:
    """True if the URL expression references caller data — an enclosing-function parameter
    or a subscript (``request.args['url']``). A module/global constant name
    (``RECAPTCHA_URL``) or a literal is not user-controlled, so not SSRF."""
    for sub in ast.walk(url):
        if isinstance(sub, ast.Name) and sub.id in params:
            return True
        if isinstance(sub, ast.Subscript):
            return True
    return False


class Ssrf(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-SSRF"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    standard_refs: ClassVar[tuple[str, ...]] = ("owasp:A10",)

    def run(self, ctx: AuditContext) -> list[Finding]:
        enclosing = nearest_enclosing_function(ctx.tree)
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not (isinstance(node, ast.Call) and _is_http_call(node)):
                continue
            url = node.args[0] if node.args else kwarg(node, "url")
            if url is None or isinstance(url, ast.Constant):
                continue
            fn = enclosing.get(id(node))
            params = function_params(fn) if fn is not None else set()
            if not _url_is_user_derived(url, params):
                continue  # constant/global URL — not caller-controlled
            out.append(
                self.make_finding(
                    ctx,
                    line=node.lineno,
                    message="outbound HTTP to a caller-derived URL; possible SSRF",
                    suggestion="validate/allow-list the destination host",
                )
            )
        return out
