"""TypeScriptAuditor — parses a TS/TSX/JS/JSX file once with tree-sitter, builds a light
manifest, runs enabled TS detectors, and applies config/role severity+verdict overrides in
one place (mirroring PythonAuditor).
"""

from typing import TYPE_CHECKING, ClassVar

from auditor.languages.base import LanguageAuditor, ShapeRow
from auditor.languages.typescript import detectors as _detectors  # noqa: F401
from auditor.languages.typescript.base import TsAuditContext
from auditor.languages.typescript.manifest import build_manifest
from auditor.languages.typescript.nodes import Tsx
from auditor.languages.typescript.parser import TsParser
from auditor.languages.typescript.shapes import ShapeExtractor
from auditor.models import FileRole, ScanResult

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

        findings, skipped = self._collect(ctx, config, rule_ids)
        return ScanResult(
            file=file_path,
            language="typescript",
            role=role,
            manifest=manifest,
            findings=findings,
            skipped_rules=skipped,
        )

    def shapes(self, source: str, *, method_min_statements: int = 3) -> list[ShapeRow]:
        # TS has its own shape extractor; the method-statement knob is Python-only
        tree = TsParser.parse(source, path="component.tsx")
        return ShapeExtractor(Tsx(tree.root_node)).shapes()
