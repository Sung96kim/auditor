"""`auditor aggregate` — roll the index up into AUDIT.md after an incremental scan."""

from _support import invoke


def test_incremental_scan_then_aggregate(sample_repo, tmp_path):
    src = str(sample_repo / "src")
    assert invoke("scan", src, "--incremental").exit_code == 0
    out = tmp_path / "AUDIT.md"
    res = invoke("aggregate", src, "-o", str(out))
    assert res.exit_code == 0
    assert out.exists() and "consolidated report" in out.read_text()
