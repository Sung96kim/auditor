"""`auditor index add|list` — register an audit scope and read it back."""

from _support import cli_json, invoke


def test_index_add_then_list(sample_repo):
    files = [str(p) for p in (sample_repo / "src").glob("*.py")][:2]
    added = invoke("index", "add", *files, "--root", str(sample_repo))
    assert added.exit_code == 0, added.output
    listed = cli_json(invoke("index", "list", "--root", str(sample_repo)))
    assert isinstance(listed, list)


def test_index_repos_then_forget(sample_repo):
    """A scanned repo shows up in `index repos`; `index forget` drops it from the shared db."""
    src = str(sample_repo / "src")
    assert invoke("scan", src, "--incremental").exit_code == 0

    before = {r["repo"] for r in cli_json(invoke("index", "repos"))}
    forgotten = cli_json(invoke("index", "forget", "--root", src))
    assert forgotten["removed"] is True
    assert forgotten["repo"] in before  # it was registered by the scan

    after = {r["repo"] for r in cli_json(invoke("index", "repos"))}
    assert forgotten["repo"] not in after


def test_index_forget_unknown_repo_is_noop(sample_repo):
    out = cli_json(invoke("index", "forget", "--root", str(sample_repo)))
    assert out["removed"] is False  # never scanned → nothing to forget
