"""Puts a recording stub of one binary on a scratch PATH; the hooks resolve `auditr-observer`
there. There is no `uvx` stub because no hook resolves `uvx` (P28)."""

import importlib.util
import json
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "plugin" / "hooks"


def plugin_module(name: str) -> ModuleType:
    """One `plugin/hooks/` script imported by path, the way the hook itself resolves it.

    `plugin/` may not import `auditor`, but a test may import both, which is how the constants
    the two sides duplicate get pinned against each other rather than against a third copy.
    """
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HOOKS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(HOOKS))
    return module


@pytest.fixture
def hook_module() -> Callable[[str], ModuleType]:
    """`plugin_module` as a fixture, so a test names the script and not the import mechanics."""
    return plugin_module


_RECORDER = """#!/usr/bin/env python3
import json, sys
open({log!r}, "a").write(json.dumps({{"argv": sys.argv[1:], "stdin": sys.stdin.read()}}) + "\\n")
"""


class Recorder:
    """One stubbed binary and the calls it saw."""

    def __init__(self, bin_dir: Path, log: Path) -> None:
        self.bin_dir = bin_dir
        self.log = log

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def path(self) -> str:
        return f"{self.bin_dir}:/usr/bin"


@pytest.fixture
def recorder(tmp_path: Path) -> Callable[[str], Recorder]:
    """Put a recording stub of `name` on a scratch PATH and hand back what it captured."""

    def make(name: str) -> Recorder:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        log = tmp_path / f"{name}.log"
        stub = bin_dir / name
        stub.write_text(_RECORDER.format(log=str(log)))
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return Recorder(bin_dir, log)

    return make
