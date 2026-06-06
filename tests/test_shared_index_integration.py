"""End-to-end: two separate repos scanned with --incremental write into the one global db
(under $AUDITOR_HOME, isolated by conftest), each into its own partition."""

from pathlib import Path

from auditor.engine import audit_target
from auditor.index import IndexStore
from auditor.paths import index_db_path, repo_key


def _make_repo(root: Path, marker_value: str) -> Path:
    """A realistic repo: its authored config lives in a repo-local ``.auditor/`` (also the
    repo-root marker); only the generated index should leave the repo for the global home."""
    root.mkdir(parents=True)
    (root / ".auditor").mkdir()
    (root / ".auditor" / "config.toml").write_text('extends = "base"\n')
    (root / "mod.py").write_text(f"password = '{marker_value}'\n")
    return root


async def test_two_scanned_repos_partitioned_in_global_db(tmp_path):
    repo_a = _make_repo(tmp_path / "alpha", "hunter2aaaa")
    repo_b = _make_repo(tmp_path / "beta", "hunter2bbbb")

    await audit_target(repo_a, incremental=True, root=repo_a)
    await audit_target(repo_b, incremental=True, root=repo_b)

    # the generated index went to the single shared global db...
    assert index_db_path().exists()
    # ...not into either repo's local .auditor/, which keeps only authored config
    for repo in (repo_a, repo_b):
        assert not (repo / ".auditor" / "index.db").exists()
        assert (repo / ".auditor" / "config.toml").exists()

    async with await IndexStore.connect(index_db_path(), repo_key(repo_a)) as a:
        a_files = {e.path for e in await a.files()}
        repos = {r["repo"] for r in await a.repos()}

    assert a_files == {"mod.py"}  # only alpha's file in alpha's partition
    assert {repo_key(repo_a), repo_key(repo_b)} <= repos
