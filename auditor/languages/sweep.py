"""Cross-language string-literal sweeps.

A whole class of rules — crypto-miner refs, credential-path access, packed blobs, committed
secrets — share one shape: *match every string literal in a file against a predicate*. The loop
and the finding live here once. Each language registers a **provider** that enumerates a file's
``(line, text)`` string literals; each rule defines its ``matches``/``message`` once in an
abstract base, then a thin subclass per language carries the language-prefixed ``rule_id`` (the
convention the registry filters on). Adding a sweep rule is then one base + one stub per language,
not the same loop re-implemented three times.

Providers are registered by the language packages on import, so the optional TypeScript extra
never has to be present for the Python/Bash sweeps to work.
"""

from collections.abc import Callable, Iterable
from typing import ClassVar

from auditor.languages.base import Detector
from auditor.models import Finding

#: enumerate ``(line, text)`` for every string literal in a file, given that language's ctx
LiteralProvider = Callable[[object], Iterable[tuple[int, str]]]
_PROVIDERS: dict[str, LiteralProvider] = {}


def register_literal_provider(language: str, provider: LiteralProvider) -> None:
    _PROVIDERS[language] = provider


def literals_for(language: str, ctx: object) -> Iterable[tuple[int, str]]:
    """Every ``(line, text)`` string literal for ``ctx``, or empty if the language has no
    provider registered (e.g. the TS extra isn't installed). For rules that want to inspect each
    literal once instead of via a ``matches``/``message`` pair."""
    provider = _PROVIDERS.get(language)
    return provider(ctx) if provider is not None else ()


class StringSweep(Detector):
    """Abstract base for a string-literal sweep. Override ``matches`` (and usually ``message``);
    declare ``sweep_suggestion``. Then register one thin subclass per language setting ``rule_id``
    and ``language``. ``run`` flags every literal the language's provider yields that ``matches``."""

    abstract: ClassVar[bool] = True
    sweep_suggestion: ClassVar[str] = ""

    def matches(self, text: str) -> bool:
        raise NotImplementedError

    def message(self, text: str) -> str:
        raise NotImplementedError

    def run(self, ctx: object) -> list[Finding]:  # type: ignore[override]
        provider = _PROVIDERS.get(self.language)
        if provider is None:
            return []
        return [
            self.make_finding(
                ctx,  # type: ignore[arg-type]
                line=line,
                message=self.message(text),
                suggestion=self.sweep_suggestion,
            )
            for line, text in provider(ctx)
            if self.matches(text)
        ]
