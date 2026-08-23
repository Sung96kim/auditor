"""Global data locations: the auditor home (default ~/.auditor, overridable via $AUDITOR_HOME),
the shared index db path under it, and the resolved-abspath repo key."""

import json
from pathlib import Path

from _support import git

from auditor.paths import (
    auditor_home,
    ensure_repo_dir,
    index_db_path,
    models_dir,
    partition_for,
    read_json_dict,
    read_json_dict_strict,
    repo_dir,
    repo_dir_key,
    repo_identity,
    repo_key,
    user_config_path,
    user_schema_path,
    write_json_dict,
)


def test_home_defaults_to_dot_auditor(monkeypatch):
    monkeypatch.delenv("AUDITOR_HOME", raising=False)
    assert auditor_home() == Path.home() / ".auditor"


def test_home_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "custom"))
    assert auditor_home() == tmp_path / "custom"


def test_home_expands_user(monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", "~/somewhere")
    assert auditor_home() == Path.home() / "somewhere"


def test_index_db_is_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    assert index_db_path() == tmp_path / "index.db"
    assert index_db_path().parent == auditor_home()


def test_repo_key_is_resolved_abspath(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    assert repo_key(d) == str(d.resolve())


def test_repo_key_resolves_relative_to_absolute(tmp_path, monkeypatch):
    (tmp_path / "r").mkdir()
    monkeypatch.chdir(tmp_path)
    assert repo_key(Path("r")) == str((tmp_path / "r").resolve())


def test_repo_identity_is_the_resolved_git_common_dir(git_repo):
    """Already resolved on the way out, so a symlinked temp dir cannot mint a second key."""
    assert repo_identity(git_repo) == str((git_repo / ".git").resolve())


def test_read_json_dict_tolerates_absent_torn_and_non_object(tmp_path):
    assert read_json_dict(tmp_path / "missing.json") == {}
    (tmp_path / "torn.json").write_text("{not json")
    assert read_json_dict(tmp_path / "torn.json") == {}
    (tmp_path / "list.json").write_text("[1, 2]")
    assert read_json_dict(tmp_path / "list.json") == {}
    (tmp_path / "ok.json").write_text('{"a": 1}')
    assert read_json_dict(tmp_path / "ok.json") == {"a": 1}


def test_worktree_shares_the_repo_key(git_repo, tmp_path):
    linked = tmp_path / "wt"
    git(git_repo, "worktree", "add", "-q", str(linked))
    assert repo_dir_key(linked) == repo_dir_key(git_repo)


def test_subdirectory_shares_the_repo_key(git_repo):
    nested = git_repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert repo_dir_key(nested) == repo_dir_key(git_repo)


def test_symlinked_path_shares_the_repo_key(git_repo, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(git_repo, target_is_directory=True)
    assert repo_dir_key(link) == repo_dir_key(git_repo)


def test_non_git_dir_falls_back_to_the_repo_key(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert repo_identity(plain) == repo_key(plain)


def test_repo_dir_lives_under_home_and_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    out = repo_dir(tmp_path)
    assert out == auditor_home() / "repos" / repo_dir_key(tmp_path)
    assert not out.exists()


def test_ensure_repo_dir_writes_the_breadcrumb(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    out = ensure_repo_dir(project)
    crumb = json.loads((out / "root.json").read_text())
    assert crumb["root"] == str(project.resolve())
    assert crumb["identity"] == repo_identity(project)
    assert isinstance(crumb["created_at"], int)


def test_ensure_repo_dir_keeps_an_existing_breadcrumb(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    crumb = ensure_repo_dir(project) / "root.json"
    crumb.write_text(
        json.dumps({"root": "/elsewhere", "identity": "x", "created_at": 1})
    )
    ensure_repo_dir(project)
    assert json.loads(crumb.read_text())["root"] == "/elsewhere"


def test_user_paths_sit_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    assert user_config_path() == auditor_home() / "config.json"
    assert user_schema_path() == auditor_home() / "config.schema.json"
    assert models_dir() == auditor_home() / "models"


def test_read_json_dict_strict_separates_absent_from_unusable(tmp_path):
    """The write path needs the distinction the lossy reader erases: `{}` means nothing is there
    to keep, None means there is something it must not replace."""
    assert read_json_dict_strict(tmp_path / "missing.json") == {}
    (tmp_path / "notadir").write_text("")
    assert read_json_dict_strict(tmp_path / "notadir" / "config.json") == {}
    (tmp_path / "torn.json").write_text('{"a": 1,}')
    assert read_json_dict_strict(tmp_path / "torn.json") is None
    (tmp_path / "list.json").write_text("[]")
    assert read_json_dict_strict(tmp_path / "list.json") is None
    (tmp_path / "ok.json").write_text('{"a": 1}')
    assert read_json_dict_strict(tmp_path / "ok.json") == {"a": 1}


def test_write_json_dict_replaces_in_one_step(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("stale")
    write_json_dict(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}
    assert list(tmp_path.iterdir()) == [path]  # the temp file is gone


def test_partition_for_a_repo_root_has_no_prefix(git_repo):
    part = partition_for(git_repo)
    assert part.identity == repo_identity(git_repo)
    assert part.prefix == ""


def test_partition_for_a_subdirectory_carries_a_posix_prefix(git_repo):
    nested = git_repo / "apps" / "backend"
    nested.mkdir(parents=True)
    part = partition_for(nested)
    assert part.identity == repo_identity(git_repo)  # one identity, two partitions
    assert part.prefix == "apps/backend/"


def test_partition_for_a_worktree_shares_the_identity(git_repo, tmp_path):
    linked = tmp_path / "wt"
    git(git_repo, "worktree", "add", "-q", str(linked))
    assert partition_for(linked).identity == partition_for(git_repo).identity
    assert partition_for(linked).prefix == ""


def test_partition_for_outside_git_falls_back_to_the_partition_key(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    part = partition_for(plain)
    assert part.identity == repo_key(plain)
    assert part.prefix == ""
