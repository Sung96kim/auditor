"""reporters/sarif_reporter.py: SARIF 2.1.0 output."""

import json

from _support import demo_result

from auditor.reporters import render


def test_sarif_reporter_valid():
    sarif = json.loads(render([demo_result()], "sarif"))
    assert sarif["version"] == "2.1.0"
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "auditor"
    assert "error" in {r["level"] for r in run["results"]}  # blocking -> error
    assert run["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 2
