# plugin/skills/write-detector/template.py
"""Template for a repo-local auditor detector. Copy into .auditor/plugins/ and adapt.

A detector subclasses the auditor rule base, sets its class-level metadata, and yields findings.
Confirm the exact base class + registration for your auditor version with `auditr plugins list`
and the rules the tool already ships (`auditr rules list`)."""

import ast
from typing import ClassVar, Iterator

from auditor.languages.base import Finding, Rule, Severity, VerdictKind  # verify path via plugins list


class ExampleNoBareExcept(Rule):
    rule_id: ClassVar[str] = "LOCAL-NO-BARE-EXCEPT"
    category: ClassVar[str] = "correctness"
    default_severity: ClassVar[Severity] = Severity.HIGH
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.AUTO

    def check(self, tree: ast.AST) -> Iterator[Finding]:
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                yield self.finding(line=node.lineno, message="bare `except:` — catch a specific exception")
