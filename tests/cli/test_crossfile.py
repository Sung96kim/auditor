"""`auditor crossfile` — recompute cross-file findings from the index."""

from _support import cli_json, invoke


def test_crossfile_recomputes_from_index(sample_repo):
    src = str(sample_repo / "src")
    assert invoke("scan", src, "--incremental").exit_code == 0
    payload = cli_json(invoke("crossfile", src))
    assert "cross_file_findings" in payload
