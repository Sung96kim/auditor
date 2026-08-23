"""Packaging contract: what ships in core and what stays behind an opt-in extra. The observer
SDKs bundle ~300 MB platform binaries, so they must never become core or dev dependencies."""

import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"
_WHEEL_EXTRAS = ("graph", "observer", "observer-claude", "observer-codex", "vectors")


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


def test_observer_console_script_is_declared(pyproject: dict):
    assert pyproject["project"]["scripts"]["auditr-observer"] == "auditr_observer:main"


def test_wheel_config_includes_the_observer_client(pyproject: dict):
    wheel = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["auditor", "auditr_observer.py"]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH")
def test_built_wheel_ships_the_observer_client(tmp_path: Path):
    """The wheel is the artifact users install; assert its contents, not just the source config."""
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=_ROOT,
        check=True,
        capture_output=True,
    )
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as wheel:
        names = wheel.namelist()
        metadata = wheel.read(
            next(n for n in names if n.endswith(".dist-info/METADATA"))
        )
    assert "auditr_observer.py" in names
    assert "auditor/cli/lazy.py" in names
    for extra in _WHEEL_EXTRAS:
        assert f"Provides-Extra: {extra}\n".encode() in metadata, extra
