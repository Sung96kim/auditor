"""End-to-end: the realistic `ts/app` fixture exercises *every* registered TS rule, and the
clean component produces nothing. Copied into a self-contained repo so the index/root resolve
locally (and the cross-file rules run)."""

import shutil
from pathlib import Path

from _support import TS_DATA

from auditor.config import load_config
from auditor.engine import ScanEngine
from auditor.index import IndexStore
from auditor.registry import REGISTRY

_PYPROJECT = (
    '[project]\nname = "x"\nversion = "0"\n'
    '[tool.auditor.rules."TS-STYLE-FILE-SIZE".threshold]\nfile_max_lines = 70\n'
    "[tool.auditor.design_system]\n"
    'ui_paths = ["@/components/ui"]\nshell = "@/lib/ui"\n'
    "[[tool.auditor.design_system.primitives]]\n"
    "component = \"Badge\"\nwhen_class = 'rounded-full bg-\\w+-\\d+/\\d+'\n"
    "[[tool.auditor.design_system.primitives]]\n"
    'component = "Button"\nsize_override = true\n'
)


async def _scan_app(tmp_path: Path):
    shutil.copytree(TS_DATA / "app", tmp_path / "src")
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT)
    (tmp_path / ".auditor").mkdir()
    settings = load_config(tmp_path)
    async with await IndexStore.connect(tmp_path / ".auditor" / "index.db") as index:
        return await ScanEngine.for_target(
            tmp_path / "src", settings=settings, index=index
        ).scan_path(tmp_path / "src")


async def test_app_fixture_exercises_every_ts_rule(tmp_path):
    results = await _scan_app(tmp_path)
    found = {f.rule_id for r in results for f in r.findings}
    ts_rules = {rid for rid in REGISTRY.rule_ids() if rid.startswith("TS-")}
    missing = ts_rules - found
    assert not missing, f"app fixture does not exercise: {sorted(missing)}"


async def test_clean_component_in_app_has_no_findings(tmp_path):
    results = await _scan_app(tmp_path)
    clean = next(r for r in results if r.file.endswith("Clean.tsx"))
    assert clean.findings == [], [f.rule_id for f in clean.findings]


async def test_edge_cases_file_is_a_clean_precision_guard(tmp_path):
    # every block in EdgeCases.tsx is a deliberate near-miss; none should fire
    results = await _scan_app(tmp_path)
    edge = next(r for r in results if r.file.endswith("EdgeCases.tsx"))
    assert edge.findings == [], [(f.rule_id, f.line) for f in edge.findings]
