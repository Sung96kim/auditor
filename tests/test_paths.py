"""Global data locations: the auditor home (default ~/.auditor, overridable via $AUDITOR_HOME),
the shared index db path under it, and the resolved-abspath repo key."""

from pathlib import Path

from auditor.paths import auditor_home, index_db_path, repo_key


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
