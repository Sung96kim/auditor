"""Generate `codex-plugin/` from `plugin/` (spec 19.2, spec 21).

One source of truth for the nine skills: `plugin/skills/` is authored, `codex-plugin/skills/` is
copied from it, and `--check` is what CI and `tests/plugin/test_codex_plugin.py` run so a drifted
mirror is a red test rather than a stale plugin on someone's machine.
"""

import argparse
import filecmp
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin"
TARGET = ROOT / "codex-plugin"
#: the manifest keys Codex reads that the Claude manifest also carries (spec 19.1)
SHARED_KEYS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)
#: the subset a plugin is not a plugin without. Absent keys are dropped silently otherwise, and
#: `--check` compares the mirror against the same derivation, so it would never notice.
REQUIRED_KEYS = ("name", "version")
#: `agents` and `hooks` are deliberately absent: Codex loads neither from a plugin (spec 19.3)
MANIFEST_EXTRA = {
    "description": (
        "Drive the auditor code-audit CLI/MCP from Codex: judge findings, gate PRs, scan for "
        "malware, explore the code graph, refine it, and watch it with the observer."
    ),
    "skills": "./skills/",
    "mcpServers": "./.mcp.json",
}
#: the MCP server the plugin registers. Both extras: the graph tools need `mcp`, and a Codex
#: session that wants to drive a refinement needs the runner too (spec 19.2)
MCP_JSON: dict[str, Any] = {
    "mcpServers": {
        "auditor": {
            "command": "uvx",
            "args": [
                "--python",
                "3.13",
                "--from",
                "auditr[mcp,observer-codex]",
                "auditr-mcp",
            ],
            "env": {},
        }
    }
}


def manifest() -> dict[str, Any]:
    """The Codex manifest, derived from the Claude one so the two cannot drift on identity.

    Raises:
        ValueError: the authored manifest lost a key no plugin can ship without.
    """
    claude = json.loads(
        (SOURCE / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    missing = [key for key in REQUIRED_KEYS if not claude.get(key)]
    if missing:
        raise ValueError(f"plugin/.claude-plugin/plugin.json declares no {missing}")
    return {key: claude[key] for key in SHARED_KEYS if key in claude} | MANIFEST_EXTRA


def _text(body: dict[str, Any]) -> str:
    """One generated JSON file's exact bytes, so `--check` sees formatting drift too."""
    return json.dumps(body, indent=2) + "\n"


def _write(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_text(body), encoding="utf-8")


def build(target: Path = TARGET) -> None:
    """Write the whole mirror under ``target``, replacing whatever was there.

    Takes a destination so a test can build into `tmp_path`: a build that only ever wrote into
    the checkout would repair, from inside the suite, the drift its own test exists to catch.
    """
    skills = target / "skills"
    if skills.exists():
        shutil.rmtree(skills)
    shutil.copytree(SOURCE / "skills", skills)
    _write(target / ".codex-plugin" / "plugin.json", manifest())
    _write(target / ".mcp.json", MCP_JSON)


def drift(target: Path = TARGET) -> list[str]:
    """Every way the mirror under ``target`` differs from what `build` would write."""
    plugin_json = target / ".codex-plugin" / "plugin.json"
    mcp_json = target / ".mcp.json"
    found = [
        f"{path} is missing"
        for path in (plugin_json, mcp_json, target / "skills")
        if not path.exists()
    ]
    if found:
        return found
    if plugin_json.read_text(encoding="utf-8") != _text(manifest()):
        found.append("plugin.json is stale")
    if mcp_json.read_text(encoding="utf-8") != _text(MCP_JSON):
        found.append(".mcp.json is stale")
    return found + _compared(SOURCE / "skills", target / "skills")


def _compared(left: Path, right: Path, prefix: str = "skills") -> list[str]:
    """Every file under `left` that `right` does not hold identically, and every extra one.

    Bytes, not stat signatures: `copytree` preserves mtimes, so `dircmp.diff_files` calls a
    same-size edit equal, and `cmpfiles` reads the content instead. ``prefix`` keeps the report on
    the path, not the bare basename.
    """
    diff = filecmp.dircmp(left, right)
    out = [f"{prefix}/{name} only in plugin/" for name in diff.left_only]
    out += [f"{prefix}/{name} only in codex-plugin/" for name in diff.right_only]
    _, mismatch, errors = filecmp.cmpfiles(
        left, right, diff.common_files, shallow=False
    )
    out += [f"{prefix}/{name} differs" for name in sorted(mismatch)]
    out += [f"{prefix}/{name} could not be compared" for name in sorted(errors)]
    for name in diff.common_dirs:
        out += _compared(left / name, right / name, f"{prefix}/{name}")
    return out


def main(argv: list[str] | None = None) -> int:
    summary = next(iter((__doc__ or "").splitlines()), "")
    parser = argparse.ArgumentParser(description=summary)
    parser.add_argument(
        "--check", action="store_true", help="report drift instead of writing"
    )
    args = parser.parse_args(argv)
    if not args.check:
        build()
        return 0
    found = drift()
    for line in found:
        print(f"codex-plugin drift: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
