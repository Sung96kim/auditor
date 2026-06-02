"""A large, deliberately messy module (AdminConsole + ReportsView) that concentrates issues
across every category — security, a11y, DRY, complexity, design-system, and cross-file dedup —
in one realistic file pair. Proves the auditor surfaces them all together at scale."""

import shutil
from pathlib import Path

from _support import TS_DATA

from auditor.config import load_config
from auditor.engine import ScanEngine
from auditor.index import IndexStore
from auditor.registry import REGISTRY

_PYPROJECT = (
    '[project]\nname = "x"\nversion = "0"\n'
    '[tool.auditor.rules."TS-STYLE-FILE-SIZE".threshold]\nfile_max_lines = 200\n'
    "[tool.auditor.design_system]\n"
    'ui_paths = ["@/components/ui"]\nshell = "@/lib/ui"\n'
    "[[tool.auditor.design_system.primitives]]\n"
    "component = \"Badge\"\nwhen_class = 'rounded-full bg-\\w+-\\d+/\\d+'\n"
    "[[tool.auditor.design_system.primitives]]\n"
    'component = "Button"\nsize_override = true\n'
)


async def _scan_stress(tmp_path: Path):
    shutil.copytree(TS_DATA / "stress", tmp_path / "src")
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT)
    (tmp_path / ".auditor").mkdir()
    settings = load_config(tmp_path)
    async with await IndexStore.connect(tmp_path / ".auditor" / "index.db") as index:
        return await ScanEngine.for_target(
            tmp_path / "src", settings=settings, index=index
        ).scan_path(tmp_path / "src")


async def test_stress_pair_surfaces_the_full_spread_of_issues(tmp_path):
    results = await _scan_stress(tmp_path)
    found = {f.rule_id for r in results for f in r.findings}
    ts_rules = {rid for rid in REGISTRY.rule_ids() if rid.startswith("TS-")}
    missing = ts_rules - found
    assert not missing, f"stress pair does not surface: {sorted(missing)}"


async def test_stress_pair_catches_cross_file_duplication(tmp_path):
    results = await _scan_stress(tmp_path)
    found = {f.rule_id for r in results for f in r.findings}
    assert {
        "TS-XFILE-DUP-FUNCTION",
        "TS-XFILE-DUP-COMPONENT",
        "TS-XFILE-DUP-JSX-BLOCK",
    } <= found


async def test_duplication_spanning_three_files_names_every_other_site(tmp_path):
    results = await _scan_stress(tmp_path)
    # the row component is duplicated in all three files; each site's finding should point at
    # the other two (a dup spanning >2 files, not just a pair)
    files_with_dup_component = {
        r.file
        for r in results
        for f in r.findings
        if f.rule_id == "TS-XFILE-DUP-COMPONENT"
    }
    assert len(files_with_dup_component) >= 3
    a_finding = next(
        f for r in results for f in r.findings if f.rule_id == "TS-XFILE-DUP-COMPONENT"
    )
    assert a_finding.message.count(",") >= 1  # lists multiple other sites
