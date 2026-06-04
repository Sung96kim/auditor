"""Dogfood guards: the auditor's own source is held to its own rules.

The package must contain no inline imports (a rule the auditor enforces and the project style
forbids), and — since the auditor is ordinary, non-malicious Python — it must not trip any of its
own malware/secrets/security detectors. The latter guards against detector false-positives on
normal code (a plugin-loader `import_module(var)`, an `mmap`/`CFUNCTYPE` primitive, a pattern
literal) reaching production, which per-detector unit fixtures alone did not catch."""

from pathlib import Path

import pytest

from auditor.engine import ScanEngine
from auditor.models import Category, ScanResult

_AUDITOR_PKG = Path(__file__).resolve().parent.parent / "auditor"
# categories that must be empty on the auditor's own (benign) source — any hit is a false positive
_FP_GUARDED = {Category.MALWARE, Category.SECRETS, Category.SECURITY}


@pytest.fixture
async def auditor_findings() -> list[ScanResult]:
    """Scan the auditor package once; shared across the dogfood guards."""
    return await ScanEngine.for_target(_AUDITOR_PKG).scan_path(_AUDITOR_PKG)


def _locations(
    results: list[ScanResult],
    *,
    rule_id: str | None = None,
    categories: set[Category] | None = None,
) -> list[str]:
    return [
        f"{r.file}:{x.line} ({x.rule_id})"
        for r in results
        for x in r.findings
        if (rule_id is None or x.rule_id == rule_id)
        and (categories is None or x.category in categories)
    ]


def test_no_inline_imports_in_auditor_source(auditor_findings: list[ScanResult]):
    hits = _locations(auditor_findings, rule_id="PY-STYLE-INLINE-IMPORT")
    assert not hits, "inline imports found in auditor/: " + ", ".join(hits)


def test_no_if_false_import_gates(auditor_findings: list[ScanResult]):
    hits = _locations(auditor_findings, rule_id="PY-STYLE-IF-FALSE-IMPORT")
    assert not hits, "`if False:` import gates found: " + ", ".join(hits)


def test_auditor_source_does_not_self_flag_malware_or_secrets(
    auditor_findings: list[ScanResult],
):
    hits = _locations(auditor_findings, categories=_FP_GUARDED)
    assert not hits, (
        "auditor/ source self-flagged (detector false positive): " + ", ".join(hits)
    )
