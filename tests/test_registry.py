"""registry.py: registration, queries, provenance, snapshot, save/restore."""

import threading

import pytest

from auditor.languages.base import Detector
from auditor.registry import REGISTRY, Registry

_FAKES = {
    "detector": {"rule_id": "X-STATE-RULE", "category": "custom", "language": "python"},
    "language": {"language": "x-lang", "extensions": (".xl",)},
    "reporter": {"format": "x-fmt"},
}


def _fake(
    kind: str, *, module: str = "tests.test_registry", **overrides: object
) -> type:
    """A minimal class the registry accepts for ``kind``, attributed to ``module``."""
    cls = type("_Fake", (), {**_FAKES[kind], **overrides})
    cls.__module__ = module
    return cls


def _name(kind: str) -> str:
    return str(
        _FAKES[kind][
            {"detector": "rule_id", "language": "language", "reporter": "format"}[kind]
        ]
    )


def _register(reg: Registry, kind: str, **overrides: object) -> None:
    getattr(reg, f"register_{kind}")(_fake(kind, **overrides))


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
    assert reg.sources.source_of("detector", "X-CUSTOM-RULE") == "test"

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
    assert Detector.framework is None


def test_dead_symbol_rule_registered():
    assert "PY-DEAD-SYMBOL" in REGISTRY.rule_ids()
    det = REGISTRY.detector("PY-DEAD-SYMBOL")
    assert det.category == "dead-code"
    assert det.repo_level is True


@pytest.mark.parametrize("kind", ["detector", "language", "reporter"])
def test_state_restores_every_registry_table(kind):
    """Restore undoes a registration of any plugin type, not just detectors."""
    reg = Registry()
    before = reg.state()
    _register(reg, kind)
    assert reg.sources.source_of(kind, _name(kind)) == "built-in"

    reg.restore(before)
    assert reg.state() == before
    assert _name(kind) not in {*reg.rule_ids(), *reg.languages(), *reg.formats()}


def test_sourcing_nests_and_restores_after_a_raise():
    """A nested block restores the outer source, and a plugin that raises leaves nothing behind."""
    reg = Registry()
    with reg.sources.sourcing("outer.py"):
        with reg.sources.sourcing("inner.py"):
            _register(reg, "detector", rule_id="X-NEST-INNER")
        _register(reg, "detector", rule_id="X-NEST-OUTER")
        with pytest.raises(RuntimeError), reg.sources.sourcing("boom.py"):
            raise RuntimeError("plugin exploded on import")
        _register(reg, "detector", rule_id="X-NEST-AFTER-RAISE")
    _register(reg, "detector", rule_id="X-NEST-OUTSIDE")

    assert reg.sources.source_of("detector", "X-NEST-INNER") == "inner.py"
    assert reg.sources.source_of("detector", "X-NEST-OUTER") == "outer.py"
    assert reg.sources.source_of("detector", "X-NEST-AFTER-RAISE") == "outer.py"
    assert reg.sources.source_of("detector", "X-NEST-OUTSIDE") == "built-in"


def test_sourcing_is_isolated_per_thread():
    """Two threads loading different plugins at once each record their own source."""
    reg, ready = Registry(), threading.Barrier(2)

    def load(source: str, rule_id: str) -> None:
        with reg.sources.sourcing(source):
            ready.wait(timeout=5)
            _register(reg, "detector", rule_id=rule_id)

    threads = [
        threading.Thread(target=load, args=args)
        for args in (("a.py", "X-THREAD-A"), ("b.py", "X-THREAD-B"))
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert reg.sources.source_of("detector", "X-THREAD-A") == "a.py"
    assert reg.sources.source_of("detector", "X-THREAD-B") == "b.py"


def test_a_listed_plugin_module_keeps_its_own_source():
    """A class a plugin imports belongs to that plugin, unless its own module is a plugin too."""
    reg = Registry()
    with reg.sources.sourcing("moda", listed=["moda", "modb"]):
        _register(reg, "detector", rule_id="X-LISTED", module="modb")
        _register(reg, "detector", rule_id="X-HELPER", module="moda.util")

    assert reg.sources.source_of("detector", "X-LISTED") == "modb"
    assert reg.sources.source_of("detector", "X-HELPER") == "moda"
