"""Cross-detector coverage guarantee: every registered rule has a test case somewhere
(per-module files for each detector module, plus the separately-tested fs/cross-file rules)."""

from _detector_cases import TESTED_SEPARATELY, all_cases

from auditor.registry import REGISTRY


def test_every_registered_rule_has_a_case():
    covered = {rule for rule, _, _ in all_cases()} | TESTED_SEPARATELY
    missing = REGISTRY.rule_ids() - covered
    assert not missing, f"detectors without a test case: {sorted(missing)}"


def test_no_orphan_cases():
    # every case maps to a real registered rule (catches typos / removed rules)
    registered = REGISTRY.rule_ids()
    orphans = {rule for rule, _, _ in all_cases()} - registered
    assert not orphans, f"cases for unknown rules: {sorted(orphans)}"
