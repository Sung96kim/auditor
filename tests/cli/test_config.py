"""`auditor config show` — the resolved configuration."""

from _support import cli_json, invoke


def test_config_show_reports_resolved_profile(sample_repo):
    payload = cli_json(invoke("config", "show", "--root", str(sample_repo)))
    assert payload["extends"] == "strict"
