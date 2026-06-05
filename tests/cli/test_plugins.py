"""`auditor plugins list` — loaded detectors/reporters and their source."""

from _support import cli_json, invoke


def test_plugins_list_reports_builtins(sample_repo):
    payload = cli_json(invoke("plugins", "list", "--root", str(sample_repo)))
    assert "json" in payload["reporters"]
    assert "PY-SEC-DANGEROUS-EVAL" in payload["detectors"]
