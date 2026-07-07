"""Detectors in style.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

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
