"""PythonAuditor — parses a file once, builds its manifest, runs enabled detectors,
and applies config/role severity+verdict overrides in one place.
"""

import ast
from typing import TYPE_CHECKING, ClassVar

from auditor.languages.base import AuditContext, LanguageAuditor, ShapeRow
from auditor.languages.python import detectors as _detectors  # noqa: F401
from auditor.languages.python.shapes import ShapeExtractor
from auditor.models import FileRole, ManifestEntry, ScanResult, SkippedRule

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig


def _defines_basesettings(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = (
                    base.attr
                    if isinstance(base, ast.Attribute)
                    else getattr(base, "id", "")
                )
                if name == "BaseSettings":
                    return True
    return False


class PythonAuditor(LanguageAuditor):
    language: ClassVar[str] = "python"
    extensions: ClassVar[tuple[str, ...]] = (".py", ".pyi")

    def audit(
        self,
        *,
        file_path: str,
        source: str,
        role: FileRole,
        config: "ResolvedConfig",
        project_deps: frozenset[str] = frozenset(),
        sibling_modules: tuple[str, ...] = (),
        package_root: str | None = None,
        rule_ids: list[str] | None = None,
    ) -> ScanResult:
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            # An unparseable file must not crash a whole-repo scan.
            return ScanResult(
                file=file_path,
                language="python",
                role=role,
                skipped_rules=[
                    SkippedRule(
                        rule_id="*",
                        reason=f"syntax error at line {exc.lineno}: {exc.msg}",
                    )
                ],
            )
        ctx = AuditContext(
            file_path=file_path,
            source=source,
            tree=tree,
            role=role,
            config=config,
            package_root=package_root,
            project_deps=project_deps,
            sibling_modules=sibling_modules,
            defines_basesettings=_defines_basesettings(tree),
        )
        manifest = ManifestEntry.from_module(tree)

        findings, skipped = self._collect(ctx, config, rule_ids)
        return ScanResult(
            file=file_path,
            language="python",
            role=role,
            manifest=manifest,
            findings=findings,
            skipped_rules=skipped,
        )

    def shapes(self, source: str, *, method_min_statements: int = 3) -> list[ShapeRow]:
        extractor = ShapeExtractor.for_source(source)
        if extractor is None:
            return []
        return extractor.shapes(method_min_statements=method_min_statements)
