from auditor.languages.python.detectors import graph_rules
from auditor.registry import REGISTRY


def test_graph_rules_registered():
    ids = REGISTRY.rule_ids()
    assert {
        graph_rules.GOD_CONCEPT_RULE,
        graph_rules.SCATTERED_CONCEPT_RULE,
        graph_rules.NAMING_INCONSISTENCY_RULE,
    } <= ids


def test_graph_rules_are_repo_level_candidate():
    for rid in (
        graph_rules.GOD_CONCEPT_RULE,
        graph_rules.SCATTERED_CONCEPT_RULE,
        graph_rules.NAMING_INCONSISTENCY_RULE,
    ):
        det = REGISTRY.detector(rid)
        assert det.repo_level is True
        assert det.verdict_kind.value == "candidate"
        assert det.default_severity.value == "suggestion"
        assert det().run(None) == []  # stub no-ops at per-file time
