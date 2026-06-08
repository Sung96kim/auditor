"""registry.py: registration, queries, provenance, snapshot."""

import pytest

from auditor.registry import REGISTRY, Registry


def test_builtin_registry_populated():
    assert "PY-SEC-DANGEROUS-EVAL" in REGISTRY.rule_ids()
    assert "python" in REGISTRY.languages()
    assert "security" in REGISTRY.categories()
    assert REGISTRY.detector("PY-SEC-DANGEROUS-EVAL").category == "security"


def test_language_for_path():
    cls = REGISTRY.language_for_path("a.py")
    assert cls is not None and cls.language == "python"
    assert REGISTRY.language_for_path("a.rs") is None


def test_detectors_for_language():
    py = REGISTRY.detectors_for_language("python")
    assert len(py) >= 45
    assert REGISTRY.detectors_for_language("rust") == []


def test_isolated_registry_register_and_duplicate():
    reg = Registry()

    class _D:
        rule_id = "X-CUSTOM-RULE"
        category = "custom"
        language = "python"

    reg.register_detector(_D, source="test")
    assert "X-CUSTOM-RULE" in reg.rule_ids()
    assert "custom" in reg.categories()
    assert reg.source_of("detector", "X-CUSTOM-RULE") == "test"

    class _D2:
        rule_id = "X-CUSTOM-RULE"
        category = "custom"

    with pytest.raises(ValueError, match="duplicate rule_id"):
        reg.register_detector(_D2)


def test_snapshot_shape():
    snap = REGISTRY.snapshot()
    assert "detectors" in snap and "languages" in snap and "reporters" in snap
    assert snap["detectors"]["PY-SEC-DANGEROUS-EVAL"]["source"] == "built-in"


def test_framework_tag_and_query():
    reg = Registry()

    class _D:
        rule_id = "X-FW-RULE"
        category = "testing"
        language = "python"
        framework = "pytest"

    reg.register_detector(_D, source="test")
    assert reg.frameworks() == {"pytest"}


def test_detector_framework_defaults_none():
    from auditor.languages.base import Detector

    assert Detector.framework is None
