"""The private CODEX_HOME: the file it writes, the link it makes, and the homes it reads."""

import tomllib
from pathlib import Path

import pytest

from auditor.graph.refine.codex_home import (
    BEARER_ENV,
    CodexHome,
    auth_hinted,
    codex_home_dir,
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
    assert data["projects"][str(home.root)]["trust_level"] == "trusted"


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


def test_the_auth_hint_is_the_presence_of_that_home_s_auth_json(tmp_path):
    env = {"CODEX_HOME": str(tmp_path / "ch")}
    assert auth_hinted(env) is False
    (tmp_path / "ch").mkdir()
    (tmp_path / "ch" / "auth.json").write_text("{}", encoding="utf-8")
    assert auth_hinted(env) is True


def test_the_private_home_is_a_leaf_under_the_observer_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    assert codex_home_dir().parent.name == "observer"
