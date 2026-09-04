"""The private CODEX_HOME: the file it writes, the link it makes, and the homes it reads."""

import os
import time
import tomllib
from pathlib import Path

import pytest

from auditor.graph.refine.codex_home import (
    BEARER_ENV,
    STALE_HOME_AGE_SEC,
    CodexHome,
    codex_home_dir,
    reap_stale_homes,
    run_home,
    user_codex_home,
)

URL = "http://127.0.0.1:41111/mcp"


@pytest.fixture
def home(tmp_path: Path) -> CodexHome:
    return CodexHome(
        home=tmp_path / "codex-home",
        root=tmp_path / "repo",
        server_url=URL,
        model="gpt-5.1-codex",
    )


def parsed(home: CodexHome) -> dict:
    return tomllib.loads(home.config_toml())


def test_the_config_states_every_fact_invariant_4_needs(home):
    data = parsed(home)
    assert data["model"] == "gpt-5.1-codex"
    assert data["features"]["codex_hooks"] is False
    assert data["features"]["apps"] is False
    assert data["projects"][str(home.root)]["trust_level"] == "trusted"


def test_the_builtin_apps_server_is_turned_off_so_invariant_4_sees_one_server(home):
    """A real 0.147 run registers codex_apps by default; Invariant 4 refuses any server but graph."""
    assert parsed(home)["features"]["apps"] is False


def test_there_is_exactly_one_mcp_server_and_it_is_this_run_s_shim(home):
    servers = parsed(home)["mcp_servers"]
    assert list(servers) == ["graph"]
    assert servers["graph"]["url"] == URL
    assert servers["graph"]["bearer_token_env_var"] == BEARER_ENV
    assert servers["graph"]["default_tools_approval_mode"] == "approve"


def test_no_model_configured_leaves_the_user_s_own_default_alone(tmp_path):
    home = CodexHome(home=tmp_path / "h", root=tmp_path / "r", server_url=URL)
    assert "model" not in parsed(home)


def test_a_root_with_a_quote_in_it_still_parses_as_one_table(tmp_path):
    """The trust entry takes the real checkout path, whatever characters it holds."""
    root = tmp_path / 'we"ird'
    home = CodexHome(home=tmp_path / "h", root=root, server_url=URL)
    assert parsed(home)["projects"][str(root)]["trust_level"] == "trusted"


def test_auth_is_a_symlink_so_a_rotation_through_it_is_visible(home, tmp_path):
    real = tmp_path / "user-auth.json"
    real.write_text('{"token": "first"}', encoding="utf-8")
    written = home.write(auth=real)
    link = written / "auth.json"
    assert link.is_symlink()
    real.write_text('{"token": "second"}', encoding="utf-8")
    assert link.read_text(encoding="utf-8") == '{"token": "second"}'


def test_writing_twice_replaces_the_link_rather_than_failing(home, tmp_path):
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    home.write(auth=first)
    home.write(auth=second)
    assert (home.home / "auth.json").resolve() == second.resolve()


@pytest.mark.parametrize("named", [True, False])
def test_the_user_s_home_reads_codex_home_first_never_the_sdk_default(tmp_path, named):
    """`openai_codex.client.default_codex_home()` hardcodes `~/.codex` and ignores the var."""
    env = {"CODEX_HOME": str(tmp_path / "elsewhere")} if named else {}
    assert (user_codex_home(env) == tmp_path / "elsewhere") is named


def test_the_private_home_is_a_leaf_under_the_observer_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    assert codex_home_dir().parent.name == "observer"


def test_two_runs_get_two_homes_under_the_one_parent(tmp_path):
    """H2: one shared `config.toml` let a second run send the first one at the wrong shim."""
    homes = {run_home(tmp_path) for _ in range(2)}
    assert len(homes) == 2
    assert {home.parent for home in homes} == {tmp_path}


def test_a_run_home_defaults_under_the_observer_s_own_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    assert run_home().parent == codex_home_dir()


def _auth(tmp_path: Path) -> Path:
    """A stand-in for the user's real `~/.codex/auth.json`, which the sweep must not follow."""
    real = tmp_path / "user-auth.json"
    real.write_text('{"token": "real"}', encoding="utf-8")
    return real


def _orphan(tmp_path: Path, *, auth: Path | None = None) -> Path:
    """A written home, backdated past the sweep's age the way a killed run's would be."""
    home = CodexHome(home=run_home(tmp_path), root=tmp_path, server_url=URL).write(
        auth=auth if auth is not None else _auth(tmp_path)
    )
    return _aged(home, seconds=STALE_HOME_AGE_SEC * 2)


def _aged(leaf: Path, *, seconds: float) -> Path:
    """Backdate a home's mtime, which is what the sweep reads."""
    when = time.time() - seconds
    os.utime(leaf, (when, when))
    return leaf


def test_a_home_a_killed_run_left_behind_is_swept_by_the_next_run(tmp_path):
    """M3: only the `finally` removes a home, and a SIGKILL never reaches it."""
    orphan = _orphan(tmp_path)
    assert reap_stale_homes(tmp_path) == (orphan,)
    assert not orphan.exists()


def test_a_home_a_live_run_is_still_holding_is_left_alone(tmp_path):
    """The sweep runs at the head of every run, so a concurrent run's home must survive it."""
    live = CodexHome(home=run_home(tmp_path), root=tmp_path, server_url=URL).write(
        auth=_auth(tmp_path)
    )
    assert reap_stale_homes(tmp_path) == ()
    assert (live / "config.toml").exists()


def test_sweeping_a_home_never_follows_the_link_to_the_user_s_credentials(tmp_path):
    """`rmtree` unlinks the symlink; following it would delete the real `auth.json`."""
    auth = _auth(tmp_path)
    orphan = _orphan(tmp_path, auth=auth)
    reap_stale_homes(tmp_path)
    assert not orphan.exists()
    assert auth.read_text(encoding="utf-8") == '{"token": "real"}'


def test_a_parent_no_run_has_ever_used_sweeps_nothing_and_raises_nothing(tmp_path):
    assert reap_stale_homes(tmp_path / "never-made") == ()


def test_a_stray_file_named_like_a_home_is_not_swept(tmp_path):
    """Only directories are homes; anything else under the parent is not this sweep's to remove."""
    stray = tmp_path / "run-not-a-dir"
    stray.write_text("", encoding="utf-8")
    _aged(stray, seconds=STALE_HOME_AGE_SEC * 2)
    assert reap_stale_homes(tmp_path) == ()
    assert stray.exists()
