"""The committed-secret sweep — shared across languages, carrying the comprehensive catalog in
:mod:`auditor.secrets_signatures`. Each language subclasses with a concrete ``rule_id``/``language``;
the literal provider it shares with the malware sweeps feeds it."""

from typing import ClassVar

from auditor import secrets_signatures as secrets
from auditor.languages.sweep import StringSweep, literals_for
from auditor.models import Category, Finding, Severity


class SecretSweep(StringSweep):
    """Flags any string literal whose value matches a high-confidence provider-secret format
    (AWS key, GitHub token, Stripe key, PEM private key, JWT, DB URI with password, …). Auto
    verdict: a format match is unambiguous."""

    abstract: ClassVar[bool] = True
    category: ClassVar[Category] = Category.SECRETS
    default_severity: ClassVar[Severity] = Severity.HIGH
    sweep_suggestion: ClassVar[str] = (
        "rotate this credential now; load it from a secret store / env var, never commit it"
    )

    def run(self, ctx: object) -> list[Finding]:
        out: list[Finding] = []
        for line, text in literals_for(self.language, ctx):
            name = secrets.scan(text)
            if name is not None:
                out.append(
                    self.make_finding(
                        ctx,  # type: ignore[arg-type]
                        line=line,
                        message=f"committed {name} — a live credential in source",
                        suggestion=self.sweep_suggestion,
                    )
                )
        return out
