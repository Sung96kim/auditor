"""The committed single-file bundle and the sources it was built from cannot drift apart."""

import shutil
import subprocess
from pathlib import Path

import pytest

from auditor.graph.ui_inputs import (
    STAMP,
    TEST_SUFFIXES,
    UI_DIR,
    ui_inputs,
    ui_inputs_digest,
)

#: a built single-file bundle is over a megabyte; an empty file is also "a file that exists"
_MIN_BUNDLE_BYTES = 500_000

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


def test_the_stamp_matches_the_committed_sources():
    """The bundle went two months stale under a green suite; this is the local signal (Q8).

    What it proves is that the stamp was written while `src/` looked like this. That the bundle
    beside it was also rebuilt is CI's `git diff` over the whole `dist/` directory.
    """
    assert STAMP.exists(), _REBUILD
    assert STAMP.read_text().strip() == ui_inputs_digest(), _REBUILD


@pytest.mark.parametrize("name", ["index.html", "inputs.sha256"])
def test_the_bundle_and_its_stamp_are_both_tracked(name: str):
    """`.gitignore` keeps `dist/` on purpose, and the wheel force-includes the bundle.

    Tracked rather than merely present: an untracked bundle is one a fresh clone does not get,
    and `is_file()` cannot tell the two apart.
    """
    path = UI_DIR / "dist" / name
    assert path.is_file()
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)],
        check=True,
        capture_output=True,
    )


def test_the_committed_bundle_is_a_real_build_rather_than_an_empty_file():
    """`is_file()` passes on a zero-byte `index.html`, which is what a failed build leaves."""
    assert (UI_DIR / "dist" / "index.html").stat().st_size > _MIN_BUNDLE_BYTES


def test_editing_any_source_moves_the_digest(ui_copy: Path):
    """A source change that left the digest alone would be a check that cannot fail."""
    before = ui_inputs_digest(ui_copy)
    app = ui_copy / "src" / "App.tsx"
    app.write_text(app.read_text() + "\n// touched\n", encoding="utf-8")
    assert ui_inputs_digest(ui_copy) != before


def test_renaming_a_source_moves_the_digest(ui_copy: Path):
    """The name is hashed as well as the bytes, so a pure rename is still a rebuild.

    Whatever input sorts first, never a named one: a real rename of the file this used to spell
    made the test error with `FileNotFoundError` instead of reporting anything.
    """
    before = ui_inputs_digest(ui_copy)
    first = ui_inputs(ui_copy)[0]
    first.rename(first.with_name(f"renamed-{first.name}"))
    assert ui_inputs_digest(ui_copy) != before


def test_a_carriage_return_on_checkout_does_not_demand_a_rebuild(ui_copy: Path):
    """`core.autocrlf=true` gave every text input a CR and the stamp could never be matched."""
    before = ui_inputs_digest(ui_copy)
    for path in ui_inputs(ui_copy):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert ui_inputs_digest(ui_copy) == before


def test_the_lockfile_is_an_input(ui_copy: Path):
    """`vite` and `typescript` float on `^`, so only the lockfile makes the build reproducible."""
    before = ui_inputs_digest(ui_copy)
    lock = ui_copy / "pnpm-lock.yaml"
    lock.write_text(lock.read_text() + "\n", encoding="utf-8")
    assert ui_inputs_digest(ui_copy) != before


def test_the_output_is_never_one_of_its_own_inputs():
    """A digest over `dist/` would change every build and never agree with itself.

    Any `dist` in the path, not just the immediate parent: `dist/assets/x.js` is output too.
    """
    assert not any("dist" in path.parts for path in ui_inputs())


def test_a_test_file_is_not_an_input():
    """`vite build` does not bundle them, so a vitest edit must not demand a rebuild.

    The production tuple rather than one spelling of it: `"x.test.tsx".endswith(".test.ts")` is
    False, so half the suffixes were unpinned while four `.test.tsx` files sat in the tree.
    """
    hashed = {path.name for path in ui_inputs()}
    assert "buildGraph.ts" in hashed
    assert {"Panels.test.tsx", "runs.test.ts"} & {
        p.name for p in UI_DIR.glob("src/**/*")
    }
    assert not any(name.endswith(TEST_SUFFIXES) for name in hashed)
