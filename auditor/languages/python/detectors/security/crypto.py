"""Crypto & secrets security detectors."""

import ast
import re
from typing import ClassVar

from auditor.languages.base import AuditContext
from auditor.languages.python.detectors._util import dotted_name
from auditor.languages.python.detectors.security._base import SecurityDetector
from auditor.models import Finding, Severity, VerdictKind

_WEAK_HASHES = {"hashlib.md5", "hashlib.sha1"}
_WEAK_HASH_NAMES = {"md5", "sha1"}


class WeakHash(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-WEAK-HASH"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    standard_refs: ClassVar[tuple[str, ...]] = (
        "bandit:B303",
        "bandit:B324",
        "owasp:A02",
    )

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted_name(node.func)
            weak = name in _WEAK_HASHES or (
                name == "hashlib.new"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in _WEAK_HASH_NAMES
            )
            if weak:
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"weak hash `{name}` (md5/sha1) for integrity/passwords",
                        suggestion="use sha256+ or a password KDF (bcrypt/argon2)",
                    )
                )
        return out


_RANDOM_SEC_FNS = {
    "random",
    "randint",
    "randrange",
    "choice",
    "getrandbits",
    "shuffle",
    "sample",
    "uniform",
}
# `random` is only a security problem when its output is a token/secret/key/nonce/etc.
# Sampling, shuffling, jitter, sort tiebreakers and the like are legitimate non-crypto uses,
# so flag a call only when its assignment target or enclosing function names a secret.
_SECURITY_CONTEXT = re.compile(
    r"(token|password|passwd|secret|nonce|salt|otp|csrf|credential|session[_-]?id"
    r"|api[_-]?key|private[_-]?key|secret[_-]?key|signing[_-]?key|encryption[_-]?key"
    r"|auth[_-]?code|verification)",
    re.I,
)


def _is_insecure_random(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = dotted_name(node.func)
    return name.startswith("random.") and name.split(".", 1)[1] in _RANDOM_SEC_FNS


def _random_calls(subtree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(subtree) if _is_insecure_random(n)]


class InsecureRandom(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-INSECURE-RANDOM"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B311", "owasp:A02")

    def run(self, ctx: AuditContext) -> list[Finding]:
        sensitive: dict[int, ast.Call] = {}
        for node in ast.walk(ctx.tree):
            name = _assign_target_name(node)
            if name and _SECURITY_CONTEXT.search(name):
                value = getattr(node, "value", None)
                if value is not None:
                    sensitive.update((id(c), c) for c in _random_calls(value))
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and _SECURITY_CONTEXT.search(node.name)
            ):
                sensitive.update((id(c), c) for c in _random_calls(node))

        return [
            self.make_finding(
                ctx,
                line=call.lineno,
                message=f"`{dotted_name(call.func)}(...)` is not cryptographically "
                "secure; unsafe for tokens/keys",
                suggestion="use the secrets module for security-sensitive randomness",
            )
            for call in sorted(sensitive.values(), key=lambda c: c.lineno)
        ]


_SECRET_NAME = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|apikey|private[_-]?key)", re.I
)


class HardcodedSecret(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-HARDCODED-SECRET"
    default_severity: ClassVar[Severity] = Severity.HIGH
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B105", "owasp:A07")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            target_name = _assign_target_name(node)
            if target_name and _SECRET_NAME.search(target_name):
                value = node.value
                if (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and value.value
                ):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message=f"hardcoded secret in `{target_name}`",
                            evidence=f"{target_name} = '...'",
                            suggestion="read from a BaseSettings field / secret manager, not a literal",
                        )
                    )
        return out


def _assign_target_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        tgt = node.targets[0]
        if isinstance(tgt, ast.Name):
            return tgt.id
        if isinstance(tgt, ast.Attribute):
            return tgt.attr
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None
