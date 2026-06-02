"""Verbosity logging: the engine emits per-file/summary/finding lines through loguru, and the
package stays silent until enabled."""

from pathlib import Path

import pytest
from loguru import logger

from auditor.engine import ScanEngine
from auditor.logconfig import _LEVELS, configure


@pytest.fixture
def captured():
    """Enable auditor logging into a list sink at TRACE; restore disabled state after."""
    messages: list[str] = []
    logger.enable("auditor")
    handle = logger.add(lambda m: messages.append(str(m)), level="TRACE", format="{level}|{message}")
    yield messages
    logger.remove(handle)
    logger.disable("auditor")


def _py(tmp_path: Path) -> Path:
    f = tmp_path / "m.py"
    f.write_text("def f(x):\n    eval(x)\n    return x\n")
    return f


async def test_per_file_and_finding_lines(tmp_path, captured):
    await ScanEngine.for_target(_py(tmp_path)).scan_file(_py(tmp_path))
    text = "\n".join(captured)
    assert "INFO|m.py" in text  # per-file line at INFO
    assert "TRACE|" in text and "PY-SEC-DANGEROUS-EVAL" in text  # per-finding at TRACE


async def test_scan_path_logs_summary(tmp_path, captured):
    _py(tmp_path)
    await ScanEngine.for_target(tmp_path).scan_path(tmp_path)
    assert any("scanning" in m and "1" in m for m in captured)
    assert any(m.startswith("INFO|done") and "1 files" in m for m in captured)


async def test_silent_until_enabled(tmp_path):
    # no enable() — the package disables 'auditor' on import, so the sink stays empty
    messages: list[str] = []
    handle = logger.add(lambda m: messages.append(str(m)), level="TRACE")
    try:
        await ScanEngine.for_target(_py(tmp_path)).scan_file(_py(tmp_path))
    finally:
        logger.remove(handle)
    assert messages == []


def test_verbosity_levels_map_to_the_classic_ladder():
    assert _LEVELS == {0: "WARNING", 1: "INFO", 2: "DEBUG", 3: "TRACE"}
    configure(2)  # must not raise; attaches a stderr sink
    logger.remove()
    logger.disable("auditor")
