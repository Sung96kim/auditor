"""Global data locations: the auditor home (default ~/.auditor, overridable via $AUDITOR_HOME),
the shared index db path under it, and the resolved-abspath repo key."""

import json
import zlib
from pathlib import Path

import pytest
from _support import git, invoke

import auditr_observer
from auditor import paths as paths_module
from auditor.paths import (
    auditor_home,
    daemon_json_path,
    ensure_repo_dir,
    identity_key,
    index_db_path,
    is_main_worktree,
    models_dir,
    observer_dir,
    observer_enabled,
    observer_lock_path,
    observer_log_dir,
    observer_port,
    partition_for,
    read_json_dict,
    read_json_dict_strict,
    repo_dir,
    repo_dir_for_identity,
    repo_dir_from_key,
    repo_dir_key,
    repo_identity,
    repo_key,
    repo_root_from_key,
    spool_path,
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


def test_identity_key_is_what_the_repo_dir_is_named_after(git_repo):
    assert repo_dir_key(git_repo) == identity_key(repo_identity(git_repo))
    assert identity_key("/a/.git") != identity_key("/b/.git")


def test_the_port_rule_hashes_the_resolved_home(tmp_path, monkeypatch):
    """A daemon reached through `~/.auditor` and one reached through its real path are one daemon."""
    monkeypatch.delenv("AUDITOR_OBSERVER_PORT", raising=False)
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    expected = 7490 + zlib.crc32(str(auditor_home().resolve()).encode()) % 500
    assert observer_port() == expected
    assert 7490 <= observer_port() < 7990


def test_the_port_env_var_wins_over_the_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    monkeypatch.setenv("AUDITOR_OBSERVER_PORT", "7777")
    assert observer_port() == 7777


def test_the_daemon_files_sit_beside_the_rebuild_lock_and_never_replace_it(
    tmp_path, monkeypatch
):
    """`observer/locks/` is the rebuild lock's; the daemon adds three siblings and owns no parent."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    assert observer_dir() == tmp_path / "observer"
    assert observer_lock_path() == tmp_path / "observer" / "lock"
    assert daemon_json_path() == tmp_path / "observer" / "daemon.json"
    assert observer_log_dir() == tmp_path / "observer" / "log"
    assert spool_path("abc") == repo_dir_from_key("abc") / "spool.jsonl"
    assert repo_dir_for_identity("/i/.git") == repo_dir_from_key(
        identity_key("/i/.git")
    )


def test_a_spool_key_resolves_back_to_the_repo_it_belongs_to(tmp_path, monkeypatch):
    """A restart adopts a spool by key alone, so the breadcrumb is the only way back to the root."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    root = tmp_path / "checkout"
    root.mkdir()
    key = identity_key("/i/.git")
    assert repo_root_from_key(key) is None
    ensure_repo_dir(root, identity="/i/.git")
    assert repo_root_from_key(key) == root.resolve()


@pytest.mark.parametrize(
    ("value", "port", "enabled"),
    [
        ("maybe", "abc", True),
        ("", "", True),
        ("f", " ", False),
        ("OFF", "0", False),
    ],
)
def test_a_junk_env_value_is_ignored_rather_than_fatal(
    value, port, enabled, tmp_path, monkeypatch
):
    """Every `auditr` command builds `GlobalPaths`, so a typo here must not take the CLI down."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    monkeypatch.setenv("AUDITOR_OBSERVER", value)
    monkeypatch.setenv("AUDITOR_OBSERVER_PORT", port)
    assert observer_enabled() is enabled
    assert 7490 <= observer_port() < 7990
    assert invoke("version").exit_code == 0
    assert invoke("config", "check").exit_code == 0


def test_the_client_and_the_reader_read_one_off_set():
    """`AUDITOR_OBSERVER=f` must not disable one side of the pair and leave the other on (P4)."""
    assert set(auditr_observer._OFF) == set(paths_module.OFF_VALUES)


def test_the_worktree_probe_falls_back_on_git_before_2_31(
    git_repo, tmp_path, monkeypatch
):
    """`--path-format=absolute` is git 2.31; without the fallback old git admits every worktree."""
    linked = tmp_path / "linked"
    git(git_repo, "worktree", "add", "-q", str(linked), "-b", "side")
    real = paths_module.git_output

    def old_git(root, *args):
        return None if "--path-format=absolute" in args else real(root, *args)

    monkeypatch.setattr(paths_module, "git_output", old_git)
    assert is_main_worktree(git_repo) is True
    assert is_main_worktree(linked) is False


def test_the_main_worktree_is_the_one_whose_git_dir_is_the_common_dir(
    git_repo, tmp_path
):
    """`repo_identity` collapses every worktree to one value, so it cannot answer this (spec 8.2)."""
    linked = tmp_path / "linked"
    git(git_repo, "worktree", "add", "-q", str(linked), "-b", "side")
    assert is_main_worktree(git_repo) is True
    assert is_main_worktree(linked) is False


def test_a_tree_outside_git_is_its_own_main_worktree(tmp_path):
    """Nothing is linked to anything, so the gate must not refuse a non-git root."""
    assert is_main_worktree(tmp_path) is True
