"""Packaging contract: what ships in core and what stays behind an opt-in extra. The observer
SDKs bundle ~300 MB platform binaries, so they must never become core or dev dependencies."""

import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"


def _names(requirements: list[str]) -> set[str]:
    return {re.split(r"[<>=!~\[; ]", r, maxsplit=1)[0] for r in requirements}


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads(_PYPROJECT.read_text())


@pytest.mark.parametrize(
    "package", ["numpy", "scikit-learn", "snowballstemmer", "networkx"]
)
def test_graph_libraries_are_core_dependencies(pyproject: dict, package: str):
    assert package in _names(pyproject["project"]["dependencies"])


def test_graph_extra_is_an_empty_alias(pyproject: dict):
    assert pyproject["project"]["optional-dependencies"]["graph"] == []


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ("observer-claude", ["claude-agent-sdk>=0.2,<0.3"]),
        ("observer-codex", ["openai-codex==0.147.*"]),
        ("observer", ["claude-agent-sdk>=0.2,<0.3", "openai-codex==0.147.*"]),
        ("vectors", ["sqlite-vec>=0.1.9,<0.2", "model2vec>=0.9,<0.10"]),
    ],
)
def test_opt_in_extras_are_pinned(pyproject: dict, extra: str, expected: list[str]):
    assert pyproject["project"]["optional-dependencies"][extra] == expected


@pytest.mark.parametrize(
    "sdk", ["claude-agent-sdk", "openai-codex", "sqlite-vec", "model2vec"]
)
def test_opt_in_sdks_never_reach_core_or_dev(pyproject: dict, sdk: str):
    project = pyproject["project"]
    assert sdk not in _names(project["dependencies"])
    assert sdk not in _names(project["optional-dependencies"]["dev"])
