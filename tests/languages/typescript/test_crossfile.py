"""Cross-file TS dedup: components/functions sharing a normalized shape across files flag
both sites; a structurally-unique definition stays clean. Exercises the shared shapes table
+ cross-file pass now that shape extraction is language-pluggable."""

from pathlib import Path

from auditor.config import load_config
from auditor.engine import ScanEngine
from auditor.index import IndexStore

_CARD = """
export function Card() {
  return (
    <div>
      <header><h2>Title</h2></header>
      <section><p>body</p></section>
    </div>
  );
}
"""
_CARD_DUP = _CARD.replace("Card", "Panel").replace("Title", "Heading").replace("body", "text")
_UNIQUE = """
export function Menu() {
  return (
    <nav>
      <ul><li>a</li><li>b</li></ul>
    </nav>
  );
}
"""

_FN = """
export function computeA(values: number[]) {
  const total = values.reduce((a, b) => a + b, 0);
  if (total > 0) {
    return Math.round(total);
  }
  return Math.floor(total);
}
"""
_FN_DUP = _FN.replace("computeA", "computeB")


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="strict"\n'
    )
    (tmp_path / ".auditor").mkdir()
    src = tmp_path / "src"
    src.mkdir()
    return src


async def _scan(tmp_path: Path, files: dict[str, str]) -> dict[str, set[str]]:
    src = _repo(tmp_path)
    for name, body in files.items():
        (src / name).write_text(body)
    settings = load_config(tmp_path)
    async with await IndexStore.connect(tmp_path / ".auditor" / "index.db") as index:
        results = await ScanEngine.for_target(
            src, settings=settings, index=index
        ).scan_path(src)
    return {r.file: {f.rule_id for f in r.findings} for r in results}


async def test_duplicate_component_flags_both_sites(tmp_path):
    rules = await _scan(
        tmp_path, {"Card.tsx": _CARD, "Panel.tsx": _CARD_DUP, "Menu.tsx": _UNIQUE}
    )
    assert "TS-XFILE-DUP-COMPONENT" in rules["src/Card.tsx"]
    assert "TS-XFILE-DUP-COMPONENT" in rules["src/Panel.tsx"]
    assert "TS-XFILE-DUP-COMPONENT" not in rules["src/Menu.tsx"]


async def test_duplicate_function_flags_both_sites(tmp_path):
    rules = await _scan(tmp_path, {"a.ts": _FN, "b.ts": _FN_DUP})
    assert "TS-XFILE-DUP-FUNCTION" in rules["src/a.ts"]
    assert "TS-XFILE-DUP-FUNCTION" in rules["src/b.ts"]
