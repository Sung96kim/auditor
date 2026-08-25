"""Every MCP tool resolves its root and opens the index through one seam (the S1 audit item)."""

import ast
import asyncio
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from auditor.database import IndexStore, open_repo_index
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


@pytest.mark.parametrize("module", TOOL_MODULES, ids=lambda p: p.name)
def test_no_tool_scans_while_it_holds_the_index(module: Path):
    """`audit_target` opens its own `IndexStore.connect` one level down in `engine.py`, where the
    guard above cannot see it: nesting it inside the preamble's handle puts two writers on one
    database file for the length of a repo scan."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncWith)
        and {"tool_repo", "tool_repo_at"}
        & {n for item in node.items for n in _called_names(item.context_expr)}
        and "audit_target" in {n for stmt in node.body for n in _called_names(stmt)}
    ]
    assert offenders == [], (
        f"{module.name}: the tool_repo block at line {offenders} scans inside the handle; "
        "run the scan before the block opens it"
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


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("ignore_list", {}),
        ("ignore_add", {"rule_id": "PY-STYLE-INLINE-IMPORT"}),
        ("ignore_remove", {"id": 1}),
        ("aggregate", {}),
        ("graph_clusters", {}),
    ],
)
async def test_a_migrated_tool_binds_the_checkout_identity(
    graph_repo_worktree: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    args: dict[str, object],
):
    """Five tools used to connect with no partition, so their handle's identity was the repo key
    while every other tool bound the git common dir: they addressed a different set of identity
    rows and could not see a refinement at all. Only a linked worktree tells the two apart."""
    seen: list[str] = []
    original = IndexStore.connect.__func__

    @classmethod
    async def spy(cls, db, repo, partition=None, **kwargs):
        store = await original(cls, db, repo, partition=partition, **kwargs)
        seen.append(store.partition.identity)
        return store

    monkeypatch.setattr(IndexStore, "connect", spy)
    async with Client(mcp) as client:
        await client.call_tool(tool, {**args, "path": str(graph_repo_worktree)})
    monkeypatch.undo()
    want = await open_repo_index(graph_repo_worktree)
    try:
        # the premise: in a worktree the two candidate identities really are different
        assert want.partition.identity != str(graph_repo_worktree.resolve())
        assert seen == [want.partition.identity]
    finally:
        await want.aclose()


async def test_two_index_handles_on_one_identity_do_not_deadlock(graph_repo: Path):
    """`audit_target` opens its own connection to the same database file, so a tool that scans is
    a second writer whatever the ordering. The preamble no longer spans one, and two live handles
    still have to make progress rather than wedge each other on the write lock."""
    first, second = await asyncio.gather(
        open_repo_index(graph_repo), open_repo_index(graph_repo)
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(
                first.ignores.add("A-RULE", None, None, None, None, 1.0),
                second.ignores.add("B-RULE", None, None, None, None, 1.0),
            ),
            timeout=30,
        )
        rows = sorted(row["rule_id"] for row in await first.ignores.list())
        assert rows == ["A-RULE", "B-RULE"]
    finally:
        await first.aclose()
        await second.aclose()


#: the three ways a repo policy fails to load, each already one line from the CLI (S4c-1)
BROKEN_CONFIGS = {
    "bad_profile": '[project]\nname="x"\nversion="0"\n\n[tool.auditor]\nextends = "nope"\n',
    "malformed_toml": '[project]\nname="x"\nversion=\n',
    # `respect_gitignore` is a real `AuditorSettings` field, so a bad value is a `ValidationError`;
    # `min_severity` belongs to `CategoryConfig` and would be ignored as an extra
    "bad_value": '[project]\nname="x"\nversion="0"\n\n[tool.auditor]\n'
    'respect_gitignore = "nope"\n',
}
#: every tool that reaches a repo through the preamble, with the arguments it needs to get there
PREAMBLE_TOOLS: dict[str, dict[str, object]] = {
    "graph_build": {"scan": False},
    "graph_related": {"symbol": "get_user"},
    "graph_neighbors": {"symbol": "get_user"},
    "graph_concept": {"term": "user"},
    "graph_clusters": {},
    "graph_search": {"term": "user"},
    "graph_usages": {"symbol": "get_user"},
    "graph_flow": {"symbol": "get_user"},
    "graph_overview": {},
    "graph_unresolved": {},
    "ignore_add": {"rule_id": "PY-STYLE-INLINE-IMPORT"},
    "ignore_list": {},
    "ignore_remove": {"id": 1},
    "aggregate": {},
    "finding_detail": {},
}


def _args(tool: str, repo: Path) -> dict[str, object]:
    """One tool call's arguments; `finding_detail` resolves its root from a file, not a path."""
    if tool == "finding_detail":
        return {
            "file": str(repo / "m.py"),
            "rule_id": "PY-STYLE-INLINE-IMPORT",
            "line": 1,
        }
    return {**PREAMBLE_TOOLS[tool], "path": str(repo)}


@pytest.mark.parametrize("tool", sorted(PREAMBLE_TOOLS))
@pytest.mark.parametrize("broken", sorted(BROKEN_CONFIGS))
async def test_a_config_that_does_not_load_is_one_line_from_every_tool(
    graph_repo: Path, tool: str, broken: str
):
    """The preamble is what makes this uniform: before it loaded the policy itself, twelve of
    these fifteen opened an index, ignored the config entirely and answered OK."""
    (graph_repo / "pyproject.toml").write_text(BROKEN_CONFIGS[broken])
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as raised:
            await client.call_tool(tool, _args(tool, graph_repo))
    message = str(raised.value)
    assert message.startswith("invalid config: "), message
    assert "\n" not in message and "Traceback" not in message
