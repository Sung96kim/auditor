"""Every MCP tool resolves its root and opens the index through one seam (the S1 audit item)."""

import ast
from pathlib import Path

import pytest
from fastmcp import Client

from auditor.database import open_repo_index
from auditor.mcp import mcp
from auditor.mcp.helpers import ToolRepo, tool_repo

MCP_DIR = Path(__file__).resolve().parents[2] / "auditor" / "mcp"
_NOT_TOOL_MODULES = {
    "__init__.py",
    "server.py",
    "helpers.py",
    "artifacts.py",
    "code_mode.py",
}
TOOL_MODULES = sorted(
    p for p in MCP_DIR.glob("*.py") if p.name not in _NOT_TOOL_MODULES
)


def _called_names(node: ast.AST) -> set[str]:
    """Every function or method name called at any depth under one node."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            names.add(
                func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            )
    return names


def _functions(path: Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


@pytest.mark.parametrize("module", TOOL_MODULES, ids=lambda p: p.name)
def test_no_tool_module_opens_the_index_itself(module: Path):
    names = _called_names(ast.parse(module.read_text(encoding="utf-8")))
    assert "connect" not in names, (
        f"{module.name} calls IndexStore.connect; open the index through tool_repo instead"
    )


@pytest.mark.parametrize("module", TOOL_MODULES, ids=lambda p: p.name)
def test_no_tool_resolves_a_root_beside_tool_repo(module: Path):
    """Per function, not per module: `scan` and `discover` open no index and legitimately call
    `find_root` alone, in the same module as tools that go through the preamble."""
    offenders = [
        fn.name
        for fn in _functions(module)
        if {"find_root", "tool_repo"} <= _called_names(fn)
    ]
    assert offenders == [], (
        f"{module.name}: {offenders} resolve a root next to tool_repo, which already resolved one"
    )


async def test_tool_repo_binds_the_repo_identity(graph_repo: Path):
    async with tool_repo(str(graph_repo)) as repo:
        assert isinstance(repo, ToolRepo)
        assert repo.root == graph_repo
        direct = await open_repo_index(graph_repo)
        try:
            assert repo.index.partition == direct.partition
        finally:
            await direct.aclose()


async def test_ignore_tools_see_the_repo_identity(graph_repo: Path):
    """The three ignore tools used to connect with no partition, so their handle's identity was the
    repo key: in a worktree they addressed a different set of identity rows than every other tool."""
    async with Client(mcp) as client:
        await client.call_tool(
            "ignore_add", {"rule_id": "PY-STYLE-INLINE-IMPORT", "path": str(graph_repo)}
        )
    index = await open_repo_index(graph_repo)
    try:
        assert [row["rule_id"] for row in await index.ignores.list()] == [
            "PY-STYLE-INLINE-IMPORT"
        ]
    finally:
        await index.aclose()


async def test_a_broken_config_is_a_tool_error_not_a_traceback(graph_repo: Path):
    """`respect_gitignore` is a real `AuditorSettings` field, so a bad value is a `ValidationError`.
    `min_severity` is not one: it belongs to `CategoryConfig`, and `AuditorSettings` ignores extras,
    so a config naming it loads clean and this test would assert nothing."""
    (graph_repo / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n\n[tool.auditor]\nrespect_gitignore = "nope"\n'
    )
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="invalid config"):
            await client.call_tool("discover", {"path": str(graph_repo)})


async def test_a_malformed_config_is_a_tool_error_too(graph_repo: Path):
    """The regression `format_config_error` must keep taking the whole `ConfigError` family: a
    malformed TOML is a `MalformedConfig`, which has no `.errors()`."""
    (graph_repo / "pyproject.toml").write_text('[project]\nname="x"\nversion=\n')
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="invalid config"):
            await client.call_tool("discover", {"path": str(graph_repo)})
