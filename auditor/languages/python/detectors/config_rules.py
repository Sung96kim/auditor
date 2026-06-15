"""Config-category detectors: ad-hoc env reads, import-time I/O side effects."""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import (
    dotted_name,
    nearest_enclosing_function,
)
from auditor.models import Category, Finding, Severity, VerdictKind

_ENV_CALLS = {"os.environ.get", "os.getenv"}

# Canonical env vars owned by the OS / shell / other tools (AWS CLI, ssh-agent, krew, the XDG
# base-dir spec, …). They MUST be read at their standard names — renaming them under an app prefix
# would break compatibility — so reading one is not the "move it to BaseSettings" smell.
_WELL_KNOWN_ENV = frozenset(
    {
        "HOME",
        "PATH",
        "SHELL",
        "USER",
        "LOGNAME",
        "PWD",
        "OLDPWD",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "TERM",
        "TZ",
        "EDITOR",
        "VISUAL",
        "PAGER",
        "HOSTNAME",
        "DISPLAY",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "KREW_ROOT",
        "DOCKER_CONFIG",
        "DOCKER_HOST",
        "KUBECONFIG",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "NO_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    }
)
# prefix families that are likewise tool/spec-owned (XDG_CONFIG_HOME, AWS_CONFIG_FILE, LC_ALL, …)
_WELL_KNOWN_ENV_PREFIXES = (
    "XDG_",
    "AWS_",
    "LC_",
    "SSH_",
    "GIT_",
    "DOCKER_",
    "KUBE_",
    "CONDA_",
)


def _is_well_known_env(name: str) -> bool:
    return name in _WELL_KNOWN_ENV or name.startswith(_WELL_KNOWN_ENV_PREFIXES)


def _const_str(node: ast.expr | None) -> str | None:
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


_IO_CALL_PREFIXES = (
    "requests.",
    "httpx.",
    "urllib.request.",
    "socket.",
    "subprocess.",
)
_IO_CALL_NAMES = {"open", "os.system"}


class AdhocEnv(Detector):
    rule_id: ClassVar[str] = "PY-CONFIG-ADHOC-ENV"
    category: ClassVar[Category] = Category.CONFIG
    default_severity: ClassVar[Severity] = Severity.LOW
    checklist_item: ClassVar[int] = 31

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            hit = _env_read_message(node)
            if hit is not None:
                message, suggestion = hit
                out.append(
                    self.make_finding(
                        ctx, line=node.lineno, message=message, suggestion=suggestion
                    )
                )
        return out


def _is_environ(node: ast.expr) -> bool:
    return dotted_name(node) == "os.environ"


def _env_read_message(node: ast.AST) -> tuple[str, str] | None:
    """(message, suggestion) for an ad-hoc env read, or None if ``node`` isn't one or names a
    well-known OS/tool-owned var (which must be read at its standard name, not via BaseSettings)."""
    if isinstance(node, ast.Call) and dotted_name(node.func) in _ENV_CALLS:
        var = _const_str(node.args[0]) if node.args else None
        message, suggestion = (
            "ad-hoc env read; move to a BaseSettings field",
            "add a Field on a BaseSettings subclass; read get_settings().x",
        )
    elif isinstance(node, ast.Subscript) and _is_environ(node.value):
        var = _const_str(node.slice)
        message, suggestion = (
            "ad-hoc env read (os.environ[...]); move to a BaseSettings field",
            "add a Field on a BaseSettings subclass",
        )
    else:
        return None
    if var is not None and _is_well_known_env(var):
        return None
    return message, suggestion


class ImportTimeIO(Detector):
    rule_id: ClassVar[str] = "PY-CONFIG-IMPORT-TIME-IO"
    category: ClassVar[Category] = Category.CONFIG
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE

    def run(self, ctx: AuditContext) -> list[Finding]:
        enclosing = nearest_enclosing_function(ctx.tree)
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            if enclosing.get(id(node)) is not None:
                continue  # inside a function/method body — not import-time
            # a method chained on another call's result (`requests.get(...).json()`) shares the
            # statement with the inner I/O call, which matches the same prefix and is flagged on
            # its own — skip the outer link so one chained call yields one finding, not two.
            if isinstance(node.func, ast.Attribute) and isinstance(
                node.func.value, ast.Call
            ):
                continue
            name = dotted_name(node.func)
            if name in _IO_CALL_NAMES or name.startswith(_IO_CALL_PREFIXES):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"`{name}(...)` runs at import time (module scope); side-effectful import",
                        suggestion="move I/O into a function called explicitly, not at import",
                    )
                )
        return out
