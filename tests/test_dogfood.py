"""Dogfood guards: the auditor's own source is held to a few of its own rules.

Notably, the package must contain no inline imports (a rule the auditor itself enforces and
the project style forbids) — this catches regressions before they're committed."""

from pathlib import Path

from auditor.engine import ScanEngine

_AUDITOR_PKG = Path(__file__).resolve().parent.parent / "auditor"


async def _findings_for_rule(rule_id: str) -> list[str]:
    results = await ScanEngine.for_target(_AUDITOR_PKG).scan_path(_AUDITOR_PKG)
    return [
        f"{r.file}:{x.line}"
        for r in results
        for x in r.findings
        if x.rule_id == rule_id
    ]


async def test_no_inline_imports_in_auditor_source():
    hits = await _findings_for_rule("PY-STYLE-INLINE-IMPORT")
    assert not hits, "inline imports found in auditor/: " + ", ".join(hits)


async def test_no_if_false_import_gates():
    hits = await _findings_for_rule("PY-STYLE-IF-FALSE-IMPORT")
    assert not hits, "`if False:` import gates found: " + ", ".join(hits)
