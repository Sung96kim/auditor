"""`auditor discover` — auditable files with their classified role."""

from _support import cli_json, invoke


def test_discover_reports_roles(sample_repo):
    payload = cli_json(invoke("discover", str(sample_repo)))
    roles = {f["role"] for f in payload}
    assert {"production", "test", "test_support", "script"} <= roles
