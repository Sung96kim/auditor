"""Reporter ABC + registry. A reporter renders a list of ScanResults to a string.

Plugins add formats by subclassing ``Reporter`` (auto-registered via ``__init_subclass__``).
"""

from abc import ABC, abstractmethod
from typing import ClassVar

from auditor.models import ScanResult, Severity
from auditor.registry import REGISTRY


class Reporter(ABC):
    """Render scan results to a single output string in some format."""

    format: ClassVar[str]
    abstract: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("abstract"):
            return
        if getattr(cls, "format", None):
            source = getattr(cls, "_plugin_source", "built-in")
            REGISTRY.register_reporter(cls, source=source)

    @abstractmethod
    def render(self, results: list[ScanResult]) -> str:
        raise NotImplementedError


def render(results: list[ScanResult], fmt: str) -> str:
    """Render ``results`` using the registered reporter for ``fmt``."""
    cls = REGISTRY.reporter(fmt)
    if cls is None:
        available = sorted(REGISTRY.formats())
        raise ValueError(f"unknown format {fmt!r}; available: {available}")
    return cls().render(results)


def severity_totals(results: list[ScanResult]) -> dict[Severity, int]:
    """Per-severity finding counts summed across all results."""
    out = {s: 0 for s in Severity}
    for result in results:
        for severity, count in result.counts.items():
            out[severity] += count
    return out
