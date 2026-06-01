"""languages/python/detectors/xfile.py: cross-file rules are registered as repo-level
no-op detectors (the real work is the crossfile pass)."""

from auditor.languages.python.detectors.xfile import DuplicateFunction, DuplicateModel
from auditor.registry import REGISTRY

_XFILE_RULES = ("PY-XFILE-DUP-MODEL", "PY-XFILE-DUP-FUNCTION", "PY-XFILE-PARALLEL-SIBLING")


def test_xfile_rules_registered_repo_level():
    for rid in _XFILE_RULES:
        assert rid in REGISTRY.rule_ids()
        assert REGISTRY.detector(rid).repo_level is True


def test_xfile_run_is_noop():
    # repo-level detectors never produce findings per file (ctx is unused)
    assert DuplicateModel().run(None) == []
    assert DuplicateFunction().run(None) == []
