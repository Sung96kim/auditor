"""The digest of everything `vite build` reads, so a stale committed bundle is a failing test.

`auditor/graph/ui/dist/index.html` is committed and shipped in the wheel, and nothing until now
noticed when it stopped matching `src/`. Run as `python -m auditor.graph.ui_inputs --write`.
"""

import hashlib
from pathlib import Path
from typing import Annotated

import typer

UI_DIR = Path(__file__).parent / "ui"
STAMP = UI_DIR / "dist" / "inputs.sha256"

#: what the bundle is built from. `dist/` is the output, and a test or fixture is never bundled
INPUT_GLOBS = (
    "src/**/*",
    "index.html",
    "package.json",
    "pnpm-lock.yaml",
    "vite.config.ts",
    "tsconfig.json",
)
TEST_SUFFIXES = (".test.ts", ".test.tsx", ".fixture.ts")


def ui_inputs(ui_dir: Path = UI_DIR) -> list[Path]:
    """Every build input, sorted, so two machines hash one list in one order."""
    found: set[Path] = set()
    for pattern in INPUT_GLOBS:
        found.update(path for path in ui_dir.glob(pattern) if path.is_file())
    return sorted(
        path
        for path in found
        if not path.name.endswith(TEST_SUFFIXES) and "dist" not in path.parts
    )


def ui_inputs_digest(ui_dir: Path = UI_DIR) -> str:
    """One sha256 over every input's repo-relative name and its own content hash.

    Line endings are normalised first: a checkout with `core.autocrlf=true` would otherwise
    disagree with the committed stamp on every text input and demand a rebuild that cannot help.
    """
    digest = hashlib.sha256()
    for path in ui_inputs(ui_dir):
        body = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.relative_to(ui_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(body).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest()


def main(
    write: bool = typer.Option(False, "--write", help="stamp dist/inputs.sha256"),
    ui_dir: Annotated[
        Path, typer.Option("--ui-dir", help="the UI tree to hash")
    ] = UI_DIR,
) -> None:
    """Print the digest, or write it beside the bundle it describes."""
    digest = ui_inputs_digest(ui_dir)
    if write:
        (ui_dir / "dist" / "inputs.sha256").write_text(digest + "\n", encoding="utf-8")
    print(
        digest
    )  # a bare hex line on stdout: this output is read by scripts, not by people


if __name__ == "__main__":
    typer.run(main)
