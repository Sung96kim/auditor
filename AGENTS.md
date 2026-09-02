# Agent instructions

A deterministic code auditor. Three console scripts (`pyproject.toml` `[project.scripts]`):
`auditr` (CLI), `auditr-mcp` (stdio MCP server) and `auditr-observer` (a stdlib-only client at the
repo root), the first two also under an `auditor` alias. `auditor/` is the package, `plugin/` the
Claude Code plugin, `codex-plugin/` its generated Codex mirror, `assets/` the icon and runner
marks, `tests/` mirrors the package. [docs/architecture.md](docs/architecture.md) explains how the
pieces fit; read it before restructuring anything.

## Commands

```bash
uv python install 3.13                                                       # the version CI pins
uv sync --extra dev --extra mcp --extra ts                                   # what CI installs
uv run python scripts/build_codex_plugin.py --check                          # mirror drift (CI)
uv run ruff check auditor plugin auditr_observer.py tests scripts            # lint (CI)
uv run ruff format --check auditor plugin auditr_observer.py tests scripts   # format (CI); drop --check to rewrite
uv run pytest -q                                                             # full suite (CI)
uv run pytest tests/malware/test_integration.py -v                           # CI integration job; needs clamscan
uv run auditr scan .                                                         # audit the working tree
claude plugin validate ./plugin --strict                                     # plugin manifest check (CI, soft-fails)
uv run cz bump --dry-run                                                     # preview the next version
```

CI's `test` job runs the first six in that order, and `tests/test_ci_workflows.py` pins both the
ordering and the lint paths.

The graph UI lives in `auditor/graph/ui` and uses pnpm only, never npm, npx, yarn or bun:

```bash
pnpm --dir auditor/graph/ui install --frozen-lockfile
pnpm --dir auditor/graph/ui typecheck              # tsc --noEmit
pnpm --dir auditor/graph/ui test                   # vitest run
pnpm --dir auditor/graph/ui build                  # rewrites the committed dist/index.html
uv run python -m auditor.graph.ui_inputs --write   # restamps dist/inputs.sha256
```

- Never `--all-extras` locally: it adds only the observer and vectors SDK wheels, about 640 MB
  that nothing in the suite imports, and it reddens `test_drive.py`'s missing-extra test.

## Layout rules

- `auditor/cli/` is one module per command; `cli/__init__.py` is the composition root that imports
  command modules for their registration side effects and mounts the sub-apps.
- Shared seams stay at the `auditor/` top level (`engine.py`, `config.py`, `models.py`,
  `registry.py`); never bury one inside a feature package.
- `auditr_observer.py` sits outside the package and imports nothing from `auditor`, because hooks
  run constantly and `import auditor` costs about 0.17 s. It duplicates thirteen things from
  `auditor/observer/`, each pinned by a test; [docs/architecture.md](docs/architecture.md) names
  both the modules and the thirteen. Change one side and change the other.
- `plugin/` is stdlib-only and imports nothing from `auditor`; it drives the installed `auditr`.
  `statusline/auditor_status.py` and `hooks/_common.py` re-implement package helpers on the same
  rule, and `tests/plugin/` pins each pair. Those tests **may** import `auditor` to do it.
- `codex-plugin/` is generated from `plugin/`; never hand-edit it. Re-run
  `scripts/build_codex_plugin.py` and commit the mirror in the same change.
- `tests/fixtures/` holds fixture repos whose anti-patterns are intentional, so ruff and pytest
  collection both exclude it.

## Code conventions

- Imports at module top, never inside a function; `tests/test_dogfood.py` fails on an inline
  import in the package. Two deferred imports are sanctioned, both measured and documented at the
  site (`cli/lazy.py`'s graph sub-app, `graph/refine/drive.py::_codex_backend`); do not add a third.
- Everything is typed. Records are pydantic v2 models, frozen where they are values; configuration
  is `pydantic-settings`.
- Detectors, language auditors and reporters register by subclassing their ABC, so a package
  `__init__.py` imports its modules purely for that side effect (F401 is waived there).
- Suppress a finding you have judged in source: `# auditor: skip: RULE-ID` on the offending line,
  or `# auditor: skip-file: RULE-ID`, each with a short parenthetical reason.
- Comment blocks stay under the `PY-STYLE-LONG-COMMENT` floor of 3 prose lines, and only where the
  code is not self-evident.

## Testing conventions

- `asyncio_mode = "auto"`: async tests and fixtures need no marker.
- The autouse `_isolated_auditor_home` fixture in `tests/conftest.py` repoints `AUDITOR_HOME` at a
  throwaway directory, so no test touches the real shared index. Do not opt out of it.
- Reuse `tests/_support.py`: the `sample_repo` fixture and `run_audit` / `run_ts_audit` /
  `run_sh_audit`. Function-and-fixture style, `test_*.py`, `test_`-prefixed; parametrize.
- `tests/test_dogfood.py` scans `auditor/` for `PY-STYLE-INLINE-IMPORT`,
  `PY-STYLE-IF-FALSE-IMPORT` and any `malware`, `secrets` or `security` finding, and nothing else
  gates on that scan.
- New production code ships with tests; a bug fix ships with a regression test that fails without
  the fix.

## Docs

- A feature change ships with its doc update in the same change: the `docs/references/*.md` page it
  touches, `README.md` when the front page changes, and `docs/architecture.md` when the module
  layout or a pipeline changes.
- The tracked doc set carries zero em dashes, and the README command table matches
  `auditr --help`. `tests/test_doc_assets.py` pins both, plus every relative link.
- Document only what exists. `docs/superpowers/` is local, git-ignored working material; never
  link to it from the tracked set.

## Git

- Commit messages are conventional commits, `type(scope): summary`, e.g.
  `fix(graph): resolve a re-exported binding`. This repo carries no ticket keys.
- PRs squash-merge with that same title; the merged commit keeps its `(#N)` suffix.
- Never hand-edit an `auditr` version. On merge to main `.github/workflows/release.yml` runs
  `cz bump`, which drives `[project] version`, `auditor/__init__.py:__version__`, `CHANGELOG.md`,
  `uv.lock`, the commit and the tag. No manual bump, no tag push.
- `plugin/.claude-plugin/plugin.json` carries the one hand-set version. Bump it deliberately, then
  re-run `scripts/build_codex_plugin.py` so the mirror's copy follows.
