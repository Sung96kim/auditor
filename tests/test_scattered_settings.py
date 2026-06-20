"""End-to-end PY-CONFIG-SCATTERED-SETTINGS: the repo-level rule surfaces on both the stateless
and incremental scan paths, honors the config knobs, catches transitive subclasses, and the
py-class-base shapes never leak into the duplicate-shape pass."""

from pathlib import Path

import pytest

from auditor.database import IndexStore
from auditor.engine import audit_target

_RULE = "PY-CONFIG-SCATTERED-SETTINGS"
_SETTINGS_PY = "from pydantic_settings import BaseSettings\n"


def _repo(tmp_path: Path, files: dict[str, str], toml: str = "") -> Path:
    root = tmp_path / "r"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        f'[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="base"\n{toml}'
    )
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return root


def _scattered(results) -> dict[str, list[str]]:
    return {
        r.file: [f.evidence for f in r.findings if f.rule_id == _RULE]
        for r in results
        if any(f.rule_id == _RULE for f in r.findings)
    }


async def test_flags_scattered_settings_stateless(tmp_path):
    root = _repo(
        tmp_path,
        {
            "config.py": _SETTINGS_PY
            + "class AppSettings(BaseSettings):\n    x: int = 1\n",
            "stray.py": _SETTINGS_PY
            + "class StraySettings(BaseSettings):\n    y: int = 2\n",
        },
    )
    assert _scattered(await audit_target(root, root=root)) == {
        "stray.py": ["StraySettings"]
    }


async def test_flags_scattered_settings_incremental(tmp_path):
    root = _repo(
        tmp_path,
        {
            "config.py": _SETTINGS_PY
            + "class AppSettings(BaseSettings):\n    x: int = 1\n",
            "stray.py": _SETTINGS_PY
            + "class StraySettings(BaseSettings):\n    y: int = 2\n",
        },
    )
    assert _scattered(await audit_target(root, root=root, incremental=True)) == {
        "stray.py": ["StraySettings"]
    }


async def test_transitive_subclass_across_files(tmp_path):
    root = _repo(
        tmp_path,
        {
            "config.py": _SETTINGS_PY
            + "class AppSettings(BaseSettings):\n    x: int = 1\n",
            "feature.py": "from config import AppSettings\nclass FeatureSettings(AppSettings):\n    y: int = 2\n",
        },
    )
    assert _scattered(await audit_target(root, root=root)) == {
        "feature.py": ["FeatureSettings"]
    }


async def test_all_in_config_is_clean(tmp_path):
    root = _repo(
        tmp_path,
        {
            "config.py": _SETTINGS_PY
            + "class AppSettings(BaseSettings):\n    x: int = 1\n"
            + "class DbSettings(BaseSettings):\n    y: int = 2\n",
        },
    )
    assert _scattered(await audit_target(root, root=root)) == {}


async def test_configured_extra_module_silences(tmp_path):
    root = _repo(
        tmp_path,
        {
            "config.py": _SETTINGS_PY
            + "class AppSettings(BaseSettings):\n    x: int = 1\n",
            "paths.py": _SETTINGS_PY
            + "class GlobalPaths(BaseSettings):\n    home: str = '.'\n",
        },
        toml='settings_modules = ["config", "settings", "paths"]\n',
    )
    assert _scattered(await audit_target(root, root=root)) == {}


async def test_cohesion_off_flags_outlier(tmp_path):
    # cohesion off + a named config module → the outlier is still flagged (name mode alone)
    root = _repo(
        tmp_path,
        {
            "config.py": _SETTINGS_PY
            + "class AppSettings(BaseSettings):\n    x: int = 1\n",
            "stray.py": _SETTINGS_PY
            + "class StraySettings(BaseSettings):\n    y: int = 2\n",
        },
        toml="settings_cohesion = false\n",
    )
    assert _scattered(await audit_target(root, root=root)) == {
        "stray.py": ["StraySettings"]
    }


async def test_class_base_shapes_do_not_create_duplicate_findings(tmp_path):
    # two files with an identically-named non-settings class must NOT be flagged as a dup via the
    # py-class-base shapes (the dup pass skips that kind)
    root = _repo(
        tmp_path,
        {
            "a.py": "class Helper:\n    pass\n",
            "b.py": "class Helper:\n    pass\n",
        },
    )
    results = await audit_target(root, root=root, incremental=True)
    dup = [f.rule_id for r in results for f in r.findings if "XFILE-DUP" in f.rule_id]
    assert dup == []


async def test_shapes_by_kind_returns_class_base_rows(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        await s.shapes.add_shapes(
            [("h1", "py-class-base", "a.py", "Foo\x1fBaseSettings", 1)]
        )
        await s.shapes.add_shapes([("h2", "model", "a.py", "Bar", 5)])
        rows = await s.shapes.shapes_by_kind("py-class-base")
    assert [r["symbol"] for r in rows] == ["Foo\x1fBaseSettings"]


@pytest.mark.parametrize("mode", ["stateless", "incremental"])
async def test_rule_clears_after_relocation(tmp_path, mode):
    """Moving the stray settings class into config.py clears the finding on rescan."""
    root = _repo(
        tmp_path,
        {
            "config.py": _SETTINGS_PY
            + "class AppSettings(BaseSettings):\n    x: int = 1\n",
            "stray.py": _SETTINGS_PY
            + "class StraySettings(BaseSettings):\n    y: int = 2\n",
        },
    )
    inc = mode == "incremental"
    assert _scattered(await audit_target(root, root=root, incremental=inc))

    # relocate StraySettings into config.py; stray.py keeps only a non-settings class
    (root / "config.py").write_text(
        _SETTINGS_PY
        + "class AppSettings(BaseSettings):\n    x: int = 1\n"
        + "class StraySettings(BaseSettings):\n    y: int = 2\n"
    )
    (root / "stray.py").write_text("class Plain:\n    pass\n")
    assert _scattered(await audit_target(root, root=root, incremental=inc)) == {}
