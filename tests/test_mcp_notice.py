"""The MCP server notes unknown config keys on stderr, never on stdout, once per repo it is
asked about."""

import inspect

import pytest
from fastmcp import Client

import auditor.mcp.malware_tools as malware_tools
from auditor.config_notice import NOTICE, ConfigNotice
from auditor.mcp import mcp
from auditor.mcp.server import REPO_PARAMETERS

# Tools that work on no repository at all, so they must never point the notice at the server's
# own working directory. Everything else has to name its repo with one of REPO_PARAMETERS.
NO_REPO_TOOLS = frozenset({"malware_status", "malware_install"})


@pytest.fixture(autouse=True)
def _fresh_notice():
    """The notice reports each root once and the whole suite is one process, so it is reset
    around every case."""
    NOTICE.reset()
    yield
    NOTICE.reset()


def _repo(root):
    """A second repo with the same one unknown key as the shared ``bad_config`` fixture."""
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nbogus = 1\n'
    )
    (root / "a.py").write_text("x = 1\n")
    return root


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


async def test_a_call_that_names_no_repo_uses_the_tools_own_default(
    bad_config, capsys, monkeypatch
):
    """An agent calling `discover({})` relies on `path: str = "."`. The raw request carries no
    arguments at all, so reading it alone silenced the notice for the whole process."""
    monkeypatch.chdir(bad_config)
    async with Client(mcp) as client:
        await client.call_tool("discover", {})
    captured = capsys.readouterr()
    assert "bogus" in captured.err


async def test_each_repo_in_a_session_is_noted(tmp_path, capsys):
    """One server serves an agent that moves between repos: latching on the first one reported an
    arbitrary repo's keys and silenced every other repo for the process's life."""
    first, second = _repo(tmp_path / "first"), _repo(tmp_path / "second")
    async with Client(mcp) as client:
        await client.call_tool("discover", {"path": str(first)})
        await client.call_tool("discover", {"path": str(second)})
        await client.call_tool("discover", {"path": str(first)})
    captured = capsys.readouterr()
    assert captured.err.count("unknown config key") == 2


async def test_a_pre_2_settings_file_reaches_the_client_note(
    bad_config, legacy_user_config, capsys
):
    """The MCP edge says the same thing the CLI does, on one line: the settings a version bump
    moved, and where each of them went."""
    async with Client(mcp) as client:
        await client.call_tool("discover", {"path": str(bad_config)})
    captured = capsys.readouterr()
    assert all(move in captured.err for move in legacy_user_config)
    assert "auditr init --force" in captured.err
    assert ConfigNotice.MOVED not in captured.out


async def test_a_tool_that_names_no_repo_does_not_note(capsys):
    """`malware_status` works on the machine, not a repo. Noting on it would report the server's
    own working directory."""
    async with Client(mcp) as client:
        await client.call_tool("malware_status", {})
    assert "unknown config key" not in capsys.readouterr().err
    assert NOTICE.root is None


async def test_every_tool_declares_its_repo_or_declares_none():
    """The middleware finds the repo by parameter name, in the same declared schema it reads at
    call time, so a new tool calling it `repo` or `directory` would silently never be noted."""
    for tool in await mcp.list_tools():
        declared = set(tool.parameters.get("properties", {}))
        named = declared & set(REPO_PARAMETERS)
        assert bool(named) is (tool.name not in NO_REPO_TOOLS), (
            f"{tool.name}: {sorted(declared)}"
        )


def test_the_no_repo_tools_are_the_ones_that_take_nothing():
    """The frozenset above is a claim about the tools, not a licence: each really takes no
    argument that could name a repo."""
    for name in NO_REPO_TOOLS:
        assert not inspect.signature(getattr(malware_tools, name)).parameters
