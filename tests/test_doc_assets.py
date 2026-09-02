"""The tracked doc set holds itself honest: every local reference resolves, the README command
table matches the real CLI, and no page carries an em dash."""

import re
from pathlib import Path

import pytest
import typer.main

from auditor.cli import app as cli_app

_ROOT = Path(__file__).resolve().parent.parent
_IMG_SRC = re.compile(r'<img\s+[^>]*\bsrc="([^"]+)"')
_MD_LINK = re.compile(r"\]\(([^)]+)\)")
_EM_DASH = "\u2014"
_TABLE_HEADER = "| Command | What it does |"
_TABLE_ROW = re.compile(r"^\| `([a-z-]+)` \|", re.MULTILINE)


def _tracked_docs() -> list[Path]:
    """Every markdown page under docs/, skipping the git-ignored superpowers working material."""
    docs = _ROOT / "docs"
    local = docs / "superpowers"
    return sorted(p for p in docs.rglob("*.md") if local not in p.parents)


def _doc_set() -> list[Path]:
    """The writing-repo-docs set: the front page, the agent file and everything under docs/."""
    return [_ROOT / "README.md", _ROOT / "AGENTS.md", *_tracked_docs()]


def _doc_files() -> list[Path]:
    """Every page the link check reads: the doc set above, plus the asset and plugin READMEs and
    skill pages, which carry links but sit outside the prose set the em-dash pin covers."""
    return [
        *_doc_set(),
        _ROOT / "assets" / "README.md",
        *sorted((_ROOT / "plugin").rglob("*.md")),
        *sorted((_ROOT / "codex-plugin").rglob("*.md")),
    ]


def _is_url(target: str) -> bool:
    return "://" in target or target.startswith("mailto:")


def _local_targets(text: str) -> list[str]:
    raw = _IMG_SRC.findall(text) + _MD_LINK.findall(text)
    targets: list[str] = []
    for target in raw:
        target = target.split("#", 1)[0]
        if target and not _is_url(target):
            targets.append(target)
    return targets


_DOC_SET = _doc_set()
_DOC_FILES = _doc_files()
#: the set as of the S14 doc pass; a page deleted or a directory skipped must fail, not go quiet
_DOC_SET_FLOOR = 24


@pytest.mark.parametrize(
    "doc", _DOC_FILES, ids=[str(p.relative_to(_ROOT)) for p in _DOC_FILES]
)
def test_local_references_resolve(doc: Path):
    targets = _local_targets(doc.read_text())
    missing = [t for t in targets if not (doc.parent / t).resolve().exists()]
    assert not missing, (
        f"{doc.relative_to(_ROOT)}: missing local reference(s) {missing}"
    )


def test_the_doc_set_covers_every_page_it_claims():
    """The pin is parametrized, so a shrinking set would pass quietly rather than fail."""
    assert len(_DOC_SET) >= _DOC_SET_FLOOR, (
        f"the doc set is down to {len(_DOC_SET)} pages, below the pinned floor of "
        f"{_DOC_SET_FLOOR}: {[str(p.relative_to(_ROOT)) for p in _DOC_SET]}"
    )


@pytest.mark.parametrize(
    "doc", _DOC_SET, ids=[str(p.relative_to(_ROOT)) for p in _DOC_SET]
)
def test_the_doc_set_carries_no_em_dashes(doc: Path):
    """An em dash is the loudest tell that a page was machine-written; the set stays at zero."""
    hits = [
        n
        for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1)
        if _EM_DASH in line
    ]
    assert not hits, f"{doc.relative_to(_ROOT)}: em dash on line(s) {hits}"


def test_the_readme_command_table_lists_every_cli_command():
    """The table is the front page's contract with `auditr --help`; neither may gain a command
    the other does not have."""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert _TABLE_HEADER in readme, "the README command table lost its header row"
    block = readme.split(_TABLE_HEADER, 1)[1].split("\n\n", 1)[0]
    documented = set(_TABLE_ROW.findall(block))
    registered = set(typer.main.get_command(cli_app).commands)
    assert documented == registered, (
        f"table-only {sorted(documented - registered)}, "
        f"cli-only {sorted(registered - documented)}"
    )
