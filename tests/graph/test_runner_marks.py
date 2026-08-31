"""The transcribed marks against the vendored files, so the copy in the page cannot drift."""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
ASSETS = _ROOT / "assets"
MARK_TSX = _ROOT / "auditor" / "graph" / "ui" / "src" / "panels" / "RunnerMark.tsx"

#: spec 12.1 names exactly these two, and `assets/README.md` reserves the mono pair for the page
RUNNERS = [("claude", "Claude"), ("codex", "Codex")]


def _svg(stem: str) -> str:
    return (ASSETS / f"{stem}.svg").read_text(encoding="utf-8")


def _attr(markup: str, name: str) -> str | None:
    """One attribute off the first tag that carries it. The vendored files are one line each."""
    found = re.search(rf'\b{name}="([^"]*)"', markup)
    return found.group(1) if found else None


@pytest.mark.parametrize(("stem", "label"), RUNNERS)
def test_the_transcribed_path_is_the_vendored_one(stem: str, label: str):
    """The whole glyph is one `d`; a stale copy is a wrong logo nobody would notice by eye."""
    paths = re.findall(r"<path\b[^>]*>", _svg(stem))
    assert len(paths) == 1, f"{stem}.svg is no longer a single path; retranscribe it"
    drawn = _attr(paths[0], "d")
    assert drawn
    assert drawn in MARK_TSX.read_text()


@pytest.mark.parametrize(("stem", "label"), RUNNERS)
def test_the_aria_label_carries_the_runner_name(stem: str, label: str):
    """Spec 12.1 pins the label as the runner's name, which is the page's only text for it."""
    assert _attr(_svg(stem), "aria-label") == label
    assert f'label: "{label}"' in MARK_TSX.read_text()


@pytest.mark.parametrize(("stem", "label"), RUNNERS)
def test_the_vendored_mark_inherits_currentcolor(stem: str, label: str):
    """`assets/README.md` records the two deltas from upstream; the fill is what themes it."""
    markup = _svg(stem)
    assert _attr(markup, "fill") == "currentColor"
    assert _attr(markup, "role") == "img"


def test_the_component_never_reaches_for_raw_markup():
    """`TS-SEC-DANGEROUS-HTML` is this repo's own detector and `test_dogfood` refuses a self-flag."""
    source = MARK_TSX.read_text()
    assert "dangerouslySetInnerHTML" not in source
    assert "?raw" not in source


def test_no_copy_of_the_marks_lives_under_the_ui_tree():
    """One vendored pair, in `assets/`. A second copy is the drift this suite exists to stop."""
    ui = _ROOT / "auditor" / "graph" / "ui" / "src"
    assert list(ui.rglob("*.svg")) == []
