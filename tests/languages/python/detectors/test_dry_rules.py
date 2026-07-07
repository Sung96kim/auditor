"""Detectors in dry_rules.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["dry_rules"]
_TWIN = "PY-OOP-TWIN-METHODS"


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


# ---------------------------------------------------------------------------
# TwinMethods — precision guards
# ---------------------------------------------------------------------------


def test_twin_methods_zero_arg_delegates_quiet():
    # conventional pass-through delegates carry no arguments to parameterize
    src = (
        "class Conn:\n"
        "    def close(self):\n"
        "        return self._sock.close()\n"
        "class Pool:\n"
        "    def close(self):\n"
        "        return self._pool.close()\n"
    )
    assert _TWIN not in rule_ids(run_audit(src))


def test_twin_methods_single_arg_lookup_pairs_quiet():
    # `.get(x)` lookup pairs differ only in which mapping they read — nothing to parameterize
    src = (
        "class Registry:\n"
        "    def language(self, name):\n"
        "        return self._languages.get(name)\n"
        "    def reporter(self, fmt):\n"
        "        return self._reporters.get(fmt)\n"
    )
    assert _TWIN not in rule_ids(run_audit(src))


def test_twin_methods_call_free_accessors_quiet():
    # bodies without a call are too thin to be meaningful twins (accessors, constants)
    src = (
        "class Kind:\n"
        "    def user(self):\n"
        "        return 'user'\n"
        "    def admin(self):\n"
        "        return 'admin'\n"
    )
    assert _TWIN not in rule_ids(run_audit(src))


def test_twin_methods_interface_family_quiet():
    # one hook name implemented across sibling subclasses is the polymorphic-hook idiom —
    # PY-OOP-PARALLEL-SIBLING / the cross-file dup pass own that shape, not this rule
    src = (
        "class CsvSink(Sink):\n"
        "    def flush(self, rows):\n"
        "        payload = encode(rows)\n"
        "        return post(payload)\n"
        "class JsonSink(Sink):\n"
        "    def flush(self, rows):\n"
        "        payload = encode(rows)\n"
        "        return post(payload)\n"
    )
    assert _TWIN not in rule_ids(run_audit(src))


def test_twin_methods_distinct_names_across_classes_fire():
    # differently-named clone bodies in two classes are a real merge-me twin
    src = (
        "class Loader:\n"
        "    def load(self, path):\n"
        "        raw = read_file(path)\n"
        "        return parse(raw, strict=True)\n"
        "class Importer:\n"
        "    def ingest(self, path):\n"
        "        raw = read_file(path)\n"
        "        return parse(raw, strict=False)\n"
    )
    assert _TWIN in rule_ids(run_audit(src))


def test_twin_methods_finding_names_both_twins():
    src = GROUPS["dry_rules"][0][1]
    findings = [f for f in run_audit(src).findings if f.rule_id == _TWIN]
    assert len(findings) == 2
    assert "Git.feed" in findings[0].message
