"""TypeScriptAuditor — parses a TS/TSX/JS/JSX file once with tree-sitter, builds a light
manifest, runs enabled TS detectors, and applies config/role severity+verdict overrides in
one place (mirroring PythonAuditor).
"""

from typing import TYPE_CHECKING, ClassVar

from auditor.languages.base import LanguageAuditor, ShapeRow
from auditor.languages.typescript import (
    detectors as _detectors,
)  # noqa: F401  (registers TS rules)
from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.manifest import build_manifest
from auditor.languages.typescript.nodes import Tsx
from auditor.languages.typescript.parser import TsParser
from auditor.languages.typescript.shapes import ShapeExtractor
from auditor.models import FileRole, Finding, ScanResult, SkippedRule
from auditor.registry import REGISTRY

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig


class TypeScriptAuditor(LanguageAuditor):
    language: ClassVar[str] = "typescript"
    extensions: ClassVar[tuple[str, ...]] = (
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
    )

    def audit(
        self,
        *,
        file_path: str,
        source: str,
        role: FileRole,
        config: "ResolvedConfig",
        rule_ids: list[str] | None = None,
        **_: object,
    ) -> ScanResult:
        tree = TsParser.parse(source, path=file_path)
        root = Tsx(tree.root_node)
        ctx = TsAuditContext(
            file_path=file_path,
            source=source,
            root=root,
            role=role,
            config=config,
        )
        manifest = build_manifest(root)

        detector_classes = REGISTRY.detectors_for_language("typescript")
        if rule_ids is not None:
            wanted = set(rule_ids)
            detector_classes = [d for d in detector_classes if d.rule_id in wanted]

        findings: list[Finding] = []
        skipped: list[SkippedRule] = []
        for cls in detector_classes:
            eff = config.effective(cls.rule_id)
            if not eff.enabled:
                skipped.append(
                    SkippedRule(
                        rule_id=cls.rule_id, reason=eff.skipped_reason or "disabled"
                    )
                )
                continue
            detector: TsDetector = cls()
            for f in detector.run(ctx):
                findings.append(
                    f.model_copy(
                        update={
                            "severity": eff.severity,
                            "verdict_kind": eff.verdict_kind,
                        }
                    )
                )

        findings.sort(key=lambda f: (f.line, f.rule_id))
        return ScanResult(
            file=file_path,
            language="typescript",
            role=role,
            manifest=manifest,
            findings=findings,
            skipped_rules=skipped,
        )

    def shapes(self, source: str) -> list[ShapeRow]:
        tree = TsParser.parse(source, path="component.tsx")
        return ShapeExtractor(Tsx(tree.root_node)).shapes()
