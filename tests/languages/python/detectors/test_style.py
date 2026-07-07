"""Detectors in style.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

from auditor.config import AuditorSettings, RuleConfig, SizeThreshold, Threshold

_CASES = GROUPS["style"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


def test_stale_comment(tmp_path):
    (tmp_path / "real.py").write_text("x = 1\n")
    bad = "# see gone_module.py for details\nx = 1\n"
    good = "# see real.py for details\nx = 1\n"
    assert "PY-STYLE-STALE-COMMENT" in rule_ids(
        run_audit(bad, package_root=str(tmp_path))
    )
    assert "PY-STYLE-STALE-COMMENT" not in rule_ids(
        run_audit(good, package_root=str(tmp_path))
    )


@pytest.mark.parametrize(
    "name", ["setup.py", "conftest.py", "__init__.py", "manage.py"]
)
def test_stale_comment_ignores_well_known_filenames(name, tmp_path):
    # ubiquitous filenames named conceptually in prose aren't a repo-local-path claim — even
    # though absent from this repo, they must not be flagged as stale
    src = f"# behaves like a {name} would\nx = 1\n"
    assert "PY-STYLE-STALE-COMMENT" not in rule_ids(
        run_audit(src, package_root=str(tmp_path))
    )


def test_stale_comment_still_flags_ordinary_absent_module(tmp_path):
    src = "# see gone_helper.py\nx = 1\n"
    assert "PY-STYLE-STALE-COMMENT" in rule_ids(
        run_audit(src, package_root=str(tmp_path))
    )


def test_long_comment_ignores_hash_inside_string():
    src = (
        "def f():\n"
        '    template = """\n'
        "# not a comment 1\n"
        "# not a comment 2\n"
        "# not a comment 3\n"
        "# not a comment 4\n"
        "# not a comment 5\n"
        '"""\n'
        "    return template\n"
    )
    assert "PY-STYLE-LONG-COMMENT" not in rule_ids(run_audit(src))


def test_long_comment_skips_license_header():
    src = (
        "# Copyright 2026 Example Inc.\n"
        "# Licensed under the MIT License.\n"
        "# See LICENSE for details.\n"
        "# Redistribution permitted.\n"
        "# Provided as is.\n"
        "import os\n"
    )
    assert "PY-STYLE-LONG-COMMENT" not in rule_ids(run_audit(src))


def test_long_comment_ignores_trailing_comments():
    src = "x = 1  # a\ny = 2  # b\nz = 3  # c\nw = 4  # d\n"
    assert "PY-STYLE-LONG-COMMENT" not in rule_ids(run_audit(src))


def test_long_comment_flagged_after_module_docstring():
    # the docstring is code (first_code_line = 1), so the block below it is NOT preamble.
    src = (
        '"""Module doc."""\n'
        "# now a long explanatory block\n"
        "# describing the module internals\n"
        "# in far too much detail for code\n"
        "# that should speak for itself\n"
        "import os\n"
    )
    assert "PY-STYLE-LONG-COMMENT" in rule_ids(run_audit(src))


def test_long_comment_skips_pure_commented_out_function():
    src = (
        "x = 1\n"
        "# def legacy(payload):\n"
        "#     rows = parse(payload)\n"
        "#     return [r.id for r in rows]\n"
        "# legacy(None)\n"
        "y = 2\n"
    )
    assert "PY-STYLE-LONG-COMMENT" not in rule_ids(run_audit(src))


def test_long_comment_respects_configured_threshold():
    src = (
        "x = 1\n"
        "# one line of prose here\n"
        "# two lines of prose here\n"
        "# three lines of prose here\n"
        "# four lines of prose here\n"
        "# five lines of prose here\n"
        "y = 2\n"
    )
    # raise the floor to 5: a 5-prose block no longer exceeds it
    settings = AuditorSettings()
    settings.rules["PY-STYLE-LONG-COMMENT"] = RuleConfig(
        threshold=Threshold(size=SizeThreshold(comment_block_max_lines=5))
    )
    assert "PY-STYLE-LONG-COMMENT" not in rule_ids(run_audit(src, settings=settings))
    # default floor of 3: the same block IS flagged
    assert "PY-STYLE-LONG-COMMENT" in rule_ids(run_audit(src))
