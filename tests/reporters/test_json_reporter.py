"""reporters/json_reporter.py."""

import json

from _support import demo_result, result_with

from auditor.models import Severity
from auditor.reporters import render


def test_json_reporter_shape():
    payload = json.loads(render([demo_result()], "json"))
    assert payload["totals"]["blocking"] == 1
    assert payload["files"][0]["file"] == "pkg/a.py"
    assert {f["rule_id"] for f in payload["files"][0]["findings"]} == {
        "PY-SEC-DANGEROUS-EVAL",
        "PY-OOP-CONSTRUCTOR-WALL",
    }


def test_json_files_ordered_worst_severity_first():
    # a many-lows file must rank BELOW a single-blocking file; clean files come last.
    results = [
        result_with("clean.py"),
        result_with("lows.py", *[Severity.LOW] * 5),
        result_with("one_blocker.py", Severity.BLOCKING),
        result_with("highs.py", Severity.HIGH, Severity.HIGH),
    ]
    payload = json.loads(render(results, "json"))
    assert [f["file"] for f in payload["files"]] == [
        "one_blocker.py",
        "highs.py",
        "lows.py",
        "clean.py",
    ]
