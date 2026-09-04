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


def _block(stem: str) -> str:
    """The one `MARKS` entry for this runner, so a `d` cannot be checked against the wrong key.

    A whole-file substring passes when the two glyphs are transposed, which draws Claude's logo
    beside every Codex run and nothing says so.
    """
    source = MARK_TSX.read_text()
    body = source.split(f"  {stem}: {{", 1)
    assert len(body) == 2, f"MARKS has no {stem} entry"
    return body[1].split("  },", 1)[0]


@pytest.mark.parametrize(("stem", "label"), RUNNERS)
def test_the_transcribed_path_is_the_vendored_one(stem: str, label: str):
    """The whole glyph is one `d`; a stale copy is a wrong logo nobody would notice by eye."""
    paths = re.findall(r"<path\b[^>]*>", _svg(stem))
    assert len(paths) == 1, f"{stem}.svg is no longer a single path; retranscribe it"
    drawn = _attr(paths[0], "d")
    assert drawn
    assert f'd: "{drawn}"' in _block(stem)


@pytest.mark.parametrize(("stem", "label"), RUNNERS)
def test_every_drawing_attribute_is_transcribed_beside_the_path(stem: str, label: str):
    """`fill-rule` and `clip-rule` change what the glyph looks like as surely as the `d` does.

    Codex carries a `clip-rule` and Claude does not, so a copy that drops it renders a filled
    blob rather than the mark, and only the whole attribute set catches that.
    """
    markup = _svg(stem)
    block = _block(stem)
    assert f'viewBox: "{_attr(markup, "viewBox")}"' in block
    assert f'fillRule: "{_attr(markup, "fill-rule")}"' in block
    clip = _attr(re.findall(r"<path\b[^>]*>", markup)[0], "clip-rule")
    if clip is None:
        assert "clipRule" not in block
    else:
        assert f'clipRule: "{clip}"' in block


@pytest.mark.parametrize(("stem", "label"), RUNNERS)
def test_the_aria_label_carries_the_runner_name(stem: str, label: str):
    """Spec 12.1 pins the label as the runner's name, which is the page's only text for it."""
    assert _attr(_svg(stem), "aria-label") == label
    assert f'label: "{label}"' in _block(stem)


def test_the_component_names_its_mark_to_the_accessibility_tree():
    """A glyph with no `role` and no name is decoration, and the runner column would be blank."""
    source = MARK_TSX.read_text()
    assert 'role="img"' in source
    assert "aria-label={mark.label}" in source


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
