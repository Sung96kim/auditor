"""The `sample_projects/dashboard` fixture — a realistic backend+frontend app (Pulse) seeded
with a broad, real-world mess of audit flaws. Proves the auditor surfaces issues across every
category at once, runs the cross-file pass, honors roles/noqa, and excludes generated files."""

import shutil
from collections import defaultdict
from pathlib import Path

from auditor.config import load_config
from auditor.engine import ScanEngine
from auditor.index import IndexStore

_DASHBOARD = Path(__file__).parent / "fixtures" / "sample_projects" / "dashboard"

# Every category the dashboard fixture is built to exercise.
_EXPECTED_CATEGORIES = {
    "a11y",
    "async",
    "config",
    "correctness",
    "design-system",
    "oop-composition",
    "react",
    "security",
    "style",
    "typing",
}

# A representative rule per concern that must keep firing — a tripwire against fixture rot.
_REPRESENTATIVE = {
    "PY-SEC-SQL-STRING-BUILD",
    "PY-SEC-HARDCODED-SECRET",
    "PY-SEC-UNSAFE-DESERIALIZE",
    "PY-ASYNC-UNAWAITED-COROUTINE",
    "PY-ASYNC-SYNC-IO",
    "PY-CORRECT-RAISE-WITHOUT-FROM",
    "PY-CORRECT-NAIVE-DATETIME",
    "PY-CONFIG-ADHOC-ENV",
    "PY-OOP-FIELD-COPY",
    "PY-OOP-DISPATCH-LADDER",
    "PY-TYPING-UNTYPED-DICT",
    "PY-XFILE-DUP-MODEL",
    "PY-XFILE-DUP-FUNCTION",
    "TS-A11Y-DECORATIVE-ICON",
    "TS-A11Y-ICON-BUTTON-NO-LABEL",
    "TS-SEC-DANGEROUS-HTML",
    "TS-SEC-JAVASCRIPT-URL",
    "TS-REACT-ARRAY-INDEX-KEY",
    "TS-REACT-MULTI-COMPONENT-FILE",
    "TS-DS-DIRECT-UI-IMPORT",
    "TS-DS-INLINE-PRIMITIVE",
    "TS-DS-SIZE-OVERRIDE",
    "TS-XFILE-DUP-COMPONENT",
}


async def _scan(tmp_path: Path):
    proj = tmp_path / "dashboard"
    shutil.copytree(_DASHBOARD, proj)
    (proj / ".auditor").mkdir()
    settings = load_config(proj)
    async with await IndexStore.connect(proj / ".auditor" / "index.db") as index:
        return await ScanEngine.for_target(proj, settings=settings, index=index).scan_path(proj)


async def test_dashboard_exercises_every_category(tmp_path):
    results = await _scan(tmp_path)
    by_cat = defaultdict(set)
    for r in results:
        for f in r.findings:
            by_cat[str(f.category)].add(f.rule_id)
    missing = _EXPECTED_CATEGORIES - set(by_cat)
    assert not missing, f"no findings in categories: {sorted(missing)}"
    distinct = {rid for rids in by_cat.values() for rid in rids}
    assert len(distinct) >= 70, f"only {len(distinct)} distinct rules fired"


async def test_dashboard_fires_representative_rules(tmp_path):
    results = await _scan(tmp_path)
    fired = {f.rule_id for r in results for f in r.findings}
    missing = _REPRESENTATIVE - fired
    assert not missing, f"representative rules stopped firing: {sorted(missing)}"


async def test_generated_file_is_excluded(tmp_path):
    results = await _scan(tmp_path)
    assert not any(r.file.endswith(".gen.ts") for r in results)


async def test_clean_component_has_no_findings(tmp_path):
    results = await _scan(tmp_path)
    clean = next(r for r in results if r.file.endswith("Clean.tsx"))
    assert clean.findings == []


async def test_noqa_suppression_applies_in_the_project(tmp_path):
    results = await _scan(tmp_path)
    assert sum(r.suppressed for r in results) >= 1


async def test_test_file_is_classified_and_relaxed(tmp_path):
    results = await _scan(tmp_path)
    test_files = [r for r in results if r.role.value == "test"]
    assert test_files, "the backend tests/ file was not classified as a test"
    # security/typing rules are relaxed for test code — none of the prod-only noise leaks in
    assert all("PY-SEC-HARDCODED-SECRET" not in {f.rule_id for f in r.findings} for r in test_files)
