"""reporters/json_reporter.py."""

import json

from _support import demo_result, result_with

from auditor.models import Severity
from auditor.reporters import render
from auditor.reporters.json_reporter import payload


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
    rendered = json.loads(render(results, "json"))
    assert [f["file"] for f in rendered["files"]] == [
        "one_blocker.py",
        "highs.py",
        "lows.py",
        "clean.py",
    ]


def test_compact_limit_caps_to_worst_findings_and_reports_omitted():
    # 1 blocking + 2 high + 5 low = 8 findings, worst-first; cap at 3.
    results = [
        result_with("lows.py", *[Severity.LOW] * 5),
        result_with("one_blocker.py", Severity.BLOCKING),
        result_with("highs.py", Severity.HIGH, Severity.HIGH),
    ]
    out = payload(results, detail="compact", limit=3)
    shown = [f for fl in out["files"] for f in fl["findings"]]
    assert len(shown) == 3
    # the 3 kept are the worst — the blocker and both highs, not any low
    assert [f["severity"] for f in shown] == ["blocking", "high", "high"]
    assert out["omitted"] == {
        "findings": 5,
        "files": 1,
        "hint": out["omitted"]["hint"],
    }
    # the file that crosses the cap is marked truncated; totals still reflect the full scan
    assert out["totals"]["low"] == 5


def test_compact_limit_truncates_within_a_file():
    out = payload(
        [result_with("lows.py", *[Severity.LOW] * 5)], detail="compact", limit=2
    )
    assert sum(len(fl["findings"]) for fl in out["files"]) == 2
    assert out["files"][0]["truncated"] is True
    assert out["omitted"]["findings"] == 3


def test_compact_no_limit_keeps_everything():
    out = payload(
        [result_with("lows.py", *[Severity.LOW] * 5)], detail="compact", limit=None
    )
    assert sum(len(fl["findings"]) for fl in out["files"]) == 5
    assert "omitted" not in out


def test_compact_rules_map_only_covers_shown_findings():
    # capping must not carry rule metadata for findings that were omitted
    results = [
        result_with("blocker.py", Severity.BLOCKING),  # PY-TEST-RULE
        demo_result(),  # PY-SEC-DANGEROUS-EVAL + PY-OOP-CONSTRUCTOR-WALL
    ]
    out = payload(results, detail="compact", limit=1)
    shown_rule_ids = {f["rule_id"] for fl in out["files"] for f in fl["findings"]}
    assert set(out["rules"]) == shown_rule_ids
