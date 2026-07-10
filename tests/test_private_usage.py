"""PY-XFILE-PRIVATE-USED: a private (`_`-prefixed) module-level symbol referenced from another
production file should be public. Unit tests over the pure pass + one end-to-end scan."""

import pytest

from auditor.config import load_config
from auditor.database import IndexStore
from auditor.engine import ScanEngine
from auditor.private_usage import RULE_ID, find_leaked_private

_SEP = "\x1f"


def _def(path: str, name: str, *, kind: str = "func", line: int = 1) -> dict:
    return {"path": path, "symbol": f"{kind}{_SEP}{name}", "line": line}


def _ref(path: str, name: str) -> dict:
    return {"path": path, "symbol": name}


def _prod(*files: str) -> dict[str, str]:
    return {f: "production" for f in files}


def _fired(result: dict) -> set[tuple[str, str]]:
    return {(p, f.rule_id) for p, fs in result.items() for f in fs}


# --- the pure pass ---
def test_private_used_in_another_file_fires():
    out = find_leaked_private(
        [_def("a.py", "_helper")],
        [_ref("a.py", "_helper"), _ref("b.py", "_helper")],
        _prod("a.py", "b.py"),
    )
    assert ("a.py", RULE_ID) in _fired(out)
    assert "b.py" in out["a.py"][0].message  # names the other file


@pytest.mark.parametrize(
    "defs, refs, roles",
    [
        # only referenced in its own file → genuinely module-private
        ([_def("a.py", "_helper")], [_ref("a.py", "_helper")], _prod("a.py")),
        # public symbol → not our concern
        (
            [_def("a.py", "helper")],
            [_ref("a.py", "helper"), _ref("b.py", "helper")],
            _prod("a.py", "b.py"),
        ),
        # dunder → not "private" in the underscore-convention sense
        (
            [_def("a.py", "__all__", kind="const")],
            [_ref("b.py", "__all__")],
            _prod("a.py", "b.py"),
        ),
        # same private name defined in 2 files → can't attribute the ref → skip (no false positive)
        (
            [_def("a.py", "_finding"), _def("c.py", "_finding")],
            [_ref("b.py", "_finding")],
            _prod("a.py", "b.py", "c.py"),
        ),
        # def in __init__.py → re-exports live there; not flagged
        (
            [_def("pkg/__init__.py", "_x")],
            [_ref("b.py", "_x")],
            _prod("pkg/__init__.py", "b.py"),
        ),
        # def in a test file → test helpers aren't the signal
        (
            [_def("a.py", "_h")],
            [_ref("b.py", "_h")],
            {"a.py": "test", "b.py": "production"},
        ),
        # only a TEST file references it → tests poking internals isn't the signal
        (
            [_def("a.py", "_h")],
            [_ref("a.py", "_h"), _ref("t.py", "_h")],
            {"a.py": "production", "t.py": "test"},
        ),
    ],
)
def test_private_usage_clean(defs, refs, roles):
    assert find_leaked_private(defs, refs, roles) == {}


def test_reports_class_and_const_nouns():
    out = find_leaked_private(
        [_def("a.py", "_Cfg", kind="class"), _def("a.py", "_TABLE", kind="const")],
        [_ref("b.py", "_Cfg"), _ref("b.py", "_TABLE")],
        _prod("a.py", "b.py"),
    )
    msgs = " ".join(f.message for f in out["a.py"])
    assert "private class `_Cfg`" in msgs and "private constant `_TABLE`" in msgs


# --- end to end through a real scan ---
async def test_private_used_cross_file_end_to_end(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / ".auditor").mkdir()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("def _shared():\n    return 1\n")
    (pkg / "b.py").write_text(
        "from pkg.a import _shared\n\n\ndef use():\n    return _shared()\n"
    )
    settings = load_config(tmp_path)
    async with await IndexStore.connect(tmp_path / ".auditor" / "index.db") as index:
        results = {
            r.file: r
            for r in await ScanEngine.for_target(
                pkg, settings=settings, index=index
            ).scan_path(pkg)
        }
    a_rules = {f.rule_id for f in results["pkg/a.py"].findings}
    b_rules = {f.rule_id for f in results["pkg/b.py"].findings}
    assert RULE_ID in a_rules  # flagged at the definition site
    assert RULE_ID not in b_rules  # not at the use site
