"""Secret detection for config/data files: a raw-line sweep plus the committed-dotenv rule."""

from typing import ClassVar

from auditor.languages.config.base import ConfigContext, ConfigDetector
from auditor.languages.secret_sweeps import SecretSweep
from auditor.models import Category, Finding, Severity

# Dotenv variants that are meant to be committed (placeholder values, no live secrets).
_ENV_EXEMPT = (".example", ".sample", ".template", ".dist", ".defaults")


class ConfigSecretSweep(SecretSweep):
    """A committed provider credential in a config/data file (``.env``, ``.yaml``, ``.json``, …).
    Reuses the shared, format-validated secret catalog, so a config value is held to the same
    near-zero-false-positive bar as a code literal."""

    rule_id: ClassVar[str] = "CFG-SECRET-DETECTED"
    language: ClassVar[str] = "config"


class EnvFileCommitted(ConfigDetector):
    """A dotenv file tracked by the repo. The scanner already skips gitignored files, so a ``.env``
    that turns up in a scan is not gitignored — exactly the leak this rule exists to catch."""

    rule_id: ClassVar[str] = "CFG-ENV-FILE-COMMITTED"
    category: ClassVar[Category] = Category.SECRETS
    default_severity: ClassVar[Severity] = Severity.BLOCKING

    def run(self, ctx: ConfigContext) -> list[Finding]:
        name = ctx.file_path.rsplit("/", 1)[-1]
        if not _is_dotenv(name) or name.endswith(_ENV_EXEMPT):
            return []
        return [
            self.make_finding(
                ctx,
                line=1,
                message=f"`{name}` is committed to the repo — a dotenv file must not be tracked",
                suggestion="add it to .gitignore, purge it from git history, and rotate any secrets it held",
                evidence="",  # never echo the file's first line — it may hold a live secret
            )
        ]


def _is_dotenv(name: str) -> bool:
    return name == ".env" or name.startswith(".env.")
