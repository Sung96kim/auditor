"""The MCP server notes unknown config keys once per process, on stderr, never on stdout."""

import pytest
from fastmcp import Client

from auditor.config_notice import NOTICE
from auditor.mcp import mcp
from auditor.mcp.server import CONFIG_NOTICE_MIDDLEWARE


@pytest.fixture
def bad_config(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nbogus = 1\n'
    )
    (tmp_path / "a.py").write_text("x = 1\n")
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_notice():
    """The middleware notes once per process and the whole suite is one process, so both the
    notice and the registered middleware instance are reset around every case."""
    NOTICE.reset()
    CONFIG_NOTICE_MIDDLEWARE.noted = False
    yield
    NOTICE.reset()
    CONFIG_NOTICE_MIDDLEWARE.noted = False


async def test_the_note_lands_on_stderr_once(bad_config, capsys):
    async with Client(mcp) as client:
        await client.call_tool("discover", {"path": str(bad_config)})
        await client.call_tool("discover", {"path": str(bad_config)})
    captured = capsys.readouterr()
    assert captured.err.count("unknown config key") == 1
    assert "bogus" in captured.err
    assert "bogus" not in captured.out


async def test_a_file_argument_names_the_repo_too(bad_config, capsys):
    """`manifest` takes `file`, not `path`; reading only `path` would report the daemon's cwd."""
    async with Client(mcp) as client:
        await client.call_tool("manifest", {"file": str(bad_config / "a.py")})
    captured = capsys.readouterr()
    assert "bogus" in captured.err


async def test_a_tool_that_names_no_repo_does_not_burn_the_note(bad_config, capsys):
    """`rules_list` takes neither argument. Latching on it would spend the one note on the
    server's working directory and silence the repo's real typo for the process's life."""
    async with Client(mcp) as client:
        await client.call_tool("rules_list", {})
        first = capsys.readouterr()
        assert "unknown config key" not in first.err
        await client.call_tool("discover", {"path": str(bad_config)})
    captured = capsys.readouterr()
    assert "bogus" in captured.err
