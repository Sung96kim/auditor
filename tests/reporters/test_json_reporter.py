"""reporters/json_reporter.py."""

import json

from _support import demo_result

from auditor.reporters import render


def test_json_reporter_shape():
    payload = json.loads(render([demo_result()], "json"))
    assert payload["totals"]["blocking"] == 1
    assert payload["files"][0]["file"] == "pkg/a.py"
    assert {f["rule_id"] for f in payload["files"][0]["findings"]} == {
        "PY-SEC-DANGEROUS-EVAL",
        "PY-OOP-CONSTRUCTOR-WALL",
    }
