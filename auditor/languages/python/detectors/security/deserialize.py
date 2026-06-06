"""Deserialization & parsing security detectors."""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext
from auditor.languages.python.detectors._util import (
    dotted_name,
    import_alias_map,
    kwarg,
    resolve_dotted,
)
from auditor.languages.python.detectors.security._base import SecurityDetector
from auditor.models import Finding, Severity

# pickle-family loaders (and yaml.unsafe_load) that run arbitrary code on deserialization.
_UNSAFE_LOADERS = {
    "pickle.load",
    "pickle.loads",
    "_pickle.load",
    "_pickle.loads",
    "marshal.load",
    "marshal.loads",
    "cloudpickle.load",
    "cloudpickle.loads",
    "dill.load",
    "dill.loads",
    "yaml.unsafe_load",
}


class UnsafeDeserialize(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-UNSAFE-DESERIALIZE"
    default_severity: ClassVar[Severity] = Severity.HIGH
    standard_refs: ClassVar[tuple[str, ...]] = (
        "bandit:B301",
        "bandit:B506",
        "owasp:A08",
    )

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        aliases = import_alias_map(ctx.tree)
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            name = resolve_dotted(dotted_name(node.func), aliases)
            if name in _UNSAFE_LOADERS:
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"`{name}(...)` deserializes untrusted data → code execution",
                        suggestion="use a safe format (json) or validate the source",
                    )
                )
            elif name in ("yaml.load",) and not _is_safe_yaml(node):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="`yaml.load(...)` without SafeLoader can execute arbitrary objects",
                        suggestion="use yaml.safe_load or Loader=yaml.SafeLoader",
                    )
                )
        return out


def _is_safe_yaml(node: ast.Call) -> bool:
    loader = kwarg(node, "Loader")
    if loader is None and len(node.args) >= 2:
        loader = node.args[1]
    if loader is None:
        return False
    return "Safe" in dotted_name(loader)


_XML_PREFIXES = ("xml.etree", "xml.dom", "xml.sax", "lxml.etree", "ElementTree")
_XML_PARSE_ATTRS = {"parse", "fromstring", "XML", "iterparse", "parseString"}


class XxeUnsafeXml(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-XXE-UNSAFE-XML"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B313", "owasp:A05")

    def run(self, ctx: AuditContext) -> list[Finding]:
        if _imports_defusedxml(ctx.tree):
            return []
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted_name(node.func)
            attr = name.rsplit(".", 1)[-1]
            if attr in _XML_PARSE_ATTRS and name.startswith(_XML_PREFIXES):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"`{name}(...)` parses XML without defusedxml (XXE / entity expansion)",
                        suggestion="use defusedxml for untrusted XML",
                    )
                )
        return out


def _imports_defusedxml(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name.startswith("defusedxml") for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "defusedxml"
        ):
            return True
    return False
