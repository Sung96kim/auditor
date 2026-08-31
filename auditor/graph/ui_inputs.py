"""The digest of everything `vite build` reads, so a stale committed bundle is a failing test.

`auditor/graph/ui/dist/index.html` is committed and shipped in the wheel, and nothing until now
noticed when it stopped matching `src/`. Run as `python -m auditor.graph.ui_inputs --write`.
"""

import argparse
import hashlib
from pathlib import Path

UI_DIR = Path(__file__).parent / "ui"
STAMP = UI_DIR / "dist" / "inputs.sha256"

#: what the bundle is built from. `dist/` is the output, and `*.test.ts` is never bundled
INPUT_GLOBS = (
    "src/**/*",
    "index.html",
    "package.json",
    "pnpm-lock.yaml",
    "vite.config.ts",
    "tsconfig.json",
)
TEST_SUFFIXES = (".test.ts", ".test.tsx")


def ui_inputs(ui_dir: Path = UI_DIR) -> list[Path]:
    """Every build input, sorted, so two machines hash one list in one order."""
    found: set[Path] = set()
    for pattern in INPUT_GLOBS:
        found.update(path for path in ui_dir.glob(pattern) if path.is_file())
    return sorted(path for path in found if not path.name.endswith(TEST_SUFFIXES))


def ui_inputs_digest(ui_dir: Path = UI_DIR) -> str:
    """One sha256 over every input's repo-relative name and its own content hash."""
    digest = hashlib.sha256()
    for path in ui_inputs(ui_dir):
        digest.update(path.relative_to(ui_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    """Print the digest, or write it beside the bundle it describes."""
    parser = argparse.ArgumentParser(prog="auditor.graph.ui_inputs")
    parser.add_argument("--write", action="store_true", help="stamp dist/inputs.sha256")
    chosen = parser.parse_args(argv)
    digest = ui_inputs_digest()
    if chosen.write:
        STAMP.write_text(digest + "\n", encoding="utf-8")
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
