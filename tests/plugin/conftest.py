"""Stubs for the two binaries the hooks resolve on PATH: `auditr-observer` and `uvx`."""

import json
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

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
