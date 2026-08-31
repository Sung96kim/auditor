"""The committed single-file bundle and the sources it was built from cannot drift apart."""

import shutil
from pathlib import Path

import pytest

from auditor.graph.ui_inputs import STAMP, UI_DIR, ui_inputs, ui_inputs_digest

_REBUILD = (
    "auditor/graph/ui/dist/ is stale. Run `pnpm build` inside auditor/graph/ui/, then "
    "`uv run python -m auditor.graph.ui_inputs --write`."
)


@pytest.fixture
def ui_copy(tmp_path: Path) -> Path:
    """A copy of every hashed input, so a test can move one without touching the repo."""
    copy = tmp_path / "ui"
    for path in ui_inputs():
        target = copy / path.relative_to(UI_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return copy


def test_the_committed_bundle_was_built_from_the_committed_sources():
    """The bundle went two months stale under a green suite; this is the local signal (Q8)."""
    assert STAMP.exists(), _REBUILD
    assert STAMP.read_text().strip() == ui_inputs_digest(), _REBUILD


def test_the_bundle_and_its_stamp_are_both_committed():
    """`.gitignore` keeps `dist/` on purpose, and the wheel force-includes the bundle."""
    assert (UI_DIR / "dist" / "index.html").is_file()
    assert STAMP.is_file()


def test_editing_any_source_moves_the_digest(ui_copy: Path):
    """A source change that left the digest alone would be a check that cannot fail."""
    before = ui_inputs_digest(ui_copy)
    app = ui_copy / "src" / "App.tsx"
    app.write_text(app.read_text() + "\n// touched\n", encoding="utf-8")
    assert ui_inputs_digest(ui_copy) != before


def test_renaming_a_source_moves_the_digest(ui_copy: Path):
    """The name is hashed as well as the bytes, so a pure rename is still a rebuild."""
    before = ui_inputs_digest(ui_copy)
    (ui_copy / "src" / "theme.ts").rename(ui_copy / "src" / "palette.ts")
    assert ui_inputs_digest(ui_copy) != before


def test_the_lockfile_is_an_input(ui_copy: Path):
    """`vite` and `typescript` float on `^`, so only the lockfile makes the build reproducible."""
    before = ui_inputs_digest(ui_copy)
    lock = ui_copy / "pnpm-lock.yaml"
    lock.write_text(lock.read_text() + "\n", encoding="utf-8")
    assert ui_inputs_digest(ui_copy) != before


def test_the_output_is_never_one_of_its_own_inputs():
    """A digest over `dist/` would change every build and never agree with itself."""
    assert not any(path.parts[-2] == "dist" for path in ui_inputs())


def test_a_test_file_is_not_an_input():
    """`vite build` does not bundle them, so a vitest edit must not demand a rebuild."""
    hashed = {path.name for path in ui_inputs()}
    assert "buildGraph.ts" in hashed
    assert not any(name.endswith(".test.ts") for name in hashed)


def test_the_digest_is_stable_across_two_reads():
    assert ui_inputs_digest() == ui_inputs_digest()
