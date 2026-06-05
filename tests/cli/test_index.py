"""`auditor index add|list` — register an audit scope and read it back."""

from _support import cli_json, invoke


def test_index_add_then_list(sample_repo):
    files = [str(p) for p in (sample_repo / "src").glob("*.py")][:2]
    added = invoke("index", "add", *files, "--root", str(sample_repo))
    assert added.exit_code == 0, added.output
    listed = cli_json(invoke("index", "list", "--root", str(sample_repo)))
    assert isinstance(listed, list)
