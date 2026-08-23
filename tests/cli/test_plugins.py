"""`auditor plugins list` — loaded detectors/reporters and their source."""

from _support import cli_json, invoke


def test_plugins_list_reports_builtins(sample_repo):
    payload = cli_json(invoke("plugins", "list", "--root", str(sample_repo)))
    assert "json" in payload["reporters"]
    assert "PY-SEC-DANGEROUS-EVAL" in payload["detectors"]


def test_plugins_list_warns_about_unknown_config_keys(tmp_path):
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text("[malware_scan]\nbogus = 1\n")
    result = invoke("plugins", "list", "--root", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "unknown config key: malware_scan.bogus" in " ".join(result.output.split())
