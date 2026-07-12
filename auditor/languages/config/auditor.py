"""ConfigAuditor — runs the config-file detectors over data files (secret sweep + committed-dotenv).

Filenames win over extensions in ``language_for_path``, so ``package.json`` still routes to the
manifest auditor; a generic ``config.json`` / ``settings.yaml`` / ``.env`` routes here.
"""

from typing import TYPE_CHECKING, ClassVar

from auditor.languages.base import LanguageAuditor
from auditor.languages.config import (
    detectors as _detectors,  # noqa: F401  (registers the rules)
)
from auditor.languages.config.base import ConfigContext
from auditor.models import FileRole, ScanResult

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig


class ConfigAuditor(LanguageAuditor):
    language: ClassVar[str] = "config"
    #: config/data suffixes that can carry credentials. The secret catalog is format-validated,
    #: so scanning a file type that rarely holds a secret costs a cheap pass, not false positives.
    extensions: ClassVar[tuple[str, ...]] = (
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".properties",
        ".tfvars",
        ".pem",
        ".key",
    )
    #: dotenv variants and credential dotfiles, matched by name regardless of suffix
    filenames: ClassVar[tuple[str, ...]] = (
        ".env",
        ".env.*",
        ".npmrc",
        ".pypirc",
        ".netrc",
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
        ctx = ConfigContext(
            file_path=file_path, source=source, role=role, config=config
        )
        findings, skipped = self._collect(ctx, config, rule_ids)
        return ScanResult(
            file=file_path,
            language="config",
            role=role,
            manifest=[],
            findings=findings,
            skipped_rules=skipped,
        )
