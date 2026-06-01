"""builtins.py: importing it registers every built-in detector/language/reporter."""

import auditor.builtins  # noqa: F401  (import under test)
from auditor.registry import REGISTRY


def test_builtins_register_detectors():
    ids = REGISTRY.rule_ids()
    assert len(ids) >= 48  # 45 per-file + 3 cross-file repo-level rules
    # one representative from each category
    cats = {str(REGISTRY.detector(r).category) for r in ids}
    assert {
        "security",
        "typing",
        "async",
        "config",
        "correctness",
        "oop-composition",
        "style",
    } <= cats


def test_builtins_register_language_and_reporters():
    assert REGISTRY.languages().keys() >= {"python"}
    assert REGISTRY.formats() >= {"json", "sarif", "md"}
