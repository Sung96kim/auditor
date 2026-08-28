"""Every local reference in the tracked doc set must resolve: `<img src>` values and relative
markdown link targets, resolved against the referencing file's own directory."""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_IMG_SRC = re.compile(r'<img\s+[^>]*\bsrc="([^"]+)"')
_MD_LINK = re.compile(r"\]\(([^)]+)\)")


def _doc_files() -> list[Path]:
    singles = [
        _ROOT / "README.md",
        _ROOT / "AGENTS.md",
        _ROOT / "docs" / "architecture.md",
        _ROOT / "assets" / "README.md",
    ]
    references = sorted((_ROOT / "docs" / "references").glob("*.md"))
    plugin_docs = sorted((_ROOT / "plugin").rglob("*.md"))
    return singles + references + plugin_docs


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


_DOC_FILES = _doc_files()


@pytest.mark.parametrize(
    "doc", _DOC_FILES, ids=[str(p.relative_to(_ROOT)) for p in _DOC_FILES]
)
def test_local_references_resolve(doc: Path):
    targets = _local_targets(doc.read_text())
    missing = [t for t in targets if not (doc.parent / t).resolve().exists()]
    assert not missing, (
        f"{doc.relative_to(_ROOT)}: missing local reference(s) {missing}"
    )
