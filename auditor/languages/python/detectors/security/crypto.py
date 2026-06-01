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


class InsecureRandom(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-INSECURE-RANDOM"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B311", "owasp:A02")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call):
                name = dotted_name(node.func)
                if (
                    name.startswith("random.")
                    and name.split(".", 1)[1] in _RANDOM_SEC_FNS
                ):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message=f"`{name}(...)` is not cryptographically secure; unsafe for tokens/keys",
                            suggestion="use the secrets module for security-sensitive randomness",
                        )
                    )
        return out


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
