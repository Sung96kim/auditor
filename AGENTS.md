# Agent instructions

A deterministic code auditor. Three console scripts (`pyproject.toml` `[project.scripts]`):
`auditr` (CLI), `auditr-mcp` (stdio MCP server) and `auditr-observer` (a stdlib-only client module
at the repo root), each of the first two with an `auditor`-prefixed alias. `auditor/` is the
package, `plugin/` the Claude Code plugin, `assets/` the icon and the vendored runner marks,
`tests/` mirrors the package. [docs/architecture.md](docs/architecture.md) explains how the pieces
fit; read it before restructuring anything.

## Commands

```bash
uv python install 3.13                                               # the version CI pins
uv sync --extra dev --extra mcp --extra ts                           # what CI installs
uv run ruff check auditor plugin auditr_observer.py tests            # lint (CI)
uv run ruff format --check auditor plugin auditr_observer.py tests   # format check (CI); drop --check to rewrite
uv run pytest -q                                                     # full suite (CI)
uv run pytest tests/malware/test_integration.py -v                   # CI integration job; needs clamscan on PATH
uv run auditr scan .                                                 # audit the working tree
claude plugin validate ./plugin --strict                             # plugin manifest check (CI runs it when available)
uv run cz bump --dry-run                                             # preview the next version
```

The graph UI lives in `auditor/graph/ui` and uses pnpm only, never npm, npx, yarn or bun:

```bash
pnpm --dir auditor/graph/ui install --frozen-lockfile   # pnpm only, never npm or yarn
pnpm --dir auditor/graph/ui typecheck                   # tsc --noEmit
pnpm --dir auditor/graph/ui test                        # vitest run
pnpm --dir auditor/graph/ui build                       # rewrites the committed dist/index.html
uv run python -m auditor.graph.ui_inputs --write        # restamps dist/inputs.sha256
```

- Never `--all-extras` locally: it adds only the observer and vectors SDK wheels, about 640 MB
  that nothing in the suite imports.

## Layout rules

- `auditor/cli/` is one module per command; `cli/__init__.py` is the composition root that imports
  command modules for their registration side effects and mounts the sub-apps.
- Shared seams stay at the `auditor/` top level (`engine.py`, `config.py`, `models.py`,
  `registry.py`); never bury one inside a feature package.
- `auditr_observer.py` sits outside the package and imports nothing from `auditor`, because hooks
  run constantly and `import auditor` costs about 0.17 s. The daemon it talks to lives in
  `auditor/observer/`: `daemon.py` (the process, its lock and its restart), `server.py` (the
  loopback transport), `routes.py` (the handlers), `events.py` (the spool), `sessions.py` (the
  attach gate), `scheduling.py` (when a loop may act: the state enum, the quiet window, the three
  pauses, the run slots and the retry budget), `loop.py` (what it does when it may: spec 8.3's five
  work items, and every side effect `assess.py` refuses to have) and `payloads.py` (the wire
  shapes). The two sides share twelve things by duplication, each pinned by a test: the
  `OBSERVER_API_VERSION` literal, `home()` against `paths.auditor_home()`, the `_OFF` set against
  `paths.OFF_VALUES`, `STATUS_KEYS` against `DaemonStatus`, the two lifecycle timeouts against
  `SchedulingConfig`, `find_root` against `discovery.find_root`, `repo_dir_key` against
  `paths.repo_dir_key`, `parse_status_z` against `discovery.parse_status_z`, Stage 0's suffix,
  filename and excluded-directory sets against `FileDiscovery`'s, `_STATUS_ARGS` against
  `discovery._STATUS_ARGS`, `_MAX_PATHS` against `events.MAX_EVENT_PATHS`, and `spool_name`
  against `events.CLIENT_SPOOL_GLOB`.
- `plugin/` is stdlib-only and imports nothing from `auditor`; it drives the installed `auditr`.
  `plugin/statusline/auditor_status.py` and `plugin/hooks/_common.py` hand-re-implement package
  helpers and constants that `tests/plugin/` pins; change one side and change the other.
  [docs/architecture.md](docs/architecture.md) names them. Tests under `tests/plugin/` **may**
  import `auditor` in order to pin those copies against the package's own source of truth; only
  `plugin/` itself is stdlib-only.
- `tests/fixtures/` holds fixture repos whose anti-patterns are intentional, so ruff and pytest
  collection both exclude it.

## Code conventions

- Imports at module top, never inside a function; `tests/test_dogfood.py` fails on an inline
  import in the package. The one sanctioned exception is `auditor/cli/lazy.py`, which defers the
  `graph` sub-app so the fast commands never load numpy, scikit-learn or networkx. It carries the
  scoped skip directive; do not add a second deferred import.
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
- Reuse the shared scaffolding: the `sample_repo` fixture and `run_audit` / `run_ts_audit` /
  `run_sh_audit` in `tests/_support.py`. Function-and-fixture style, `test_*.py` files,
  `test_`-prefixed functions; parametrize over near-identical cases.
- `tests/test_dogfood.py` scans `auditor/` itself for three things only: `PY-STYLE-INLINE-IMPORT`,
  `PY-STYLE-IF-FALSE-IMPORT`, and any `malware`, `secrets` or `security` finding in the package.
  Nothing else gates on that scan, so the remaining candidates do not fail the build.
- New production code ships with tests; a bug fix ships with a regression test that fails without
  the fix.

## Docs

- A feature change ships with its doc update in the same change: the `docs/references/*.md` page it
  touches, `README.md` when the front page changes, and `docs/architecture.md` when the module
  layout or a pipeline changes.
- Document only what exists. `docs/superpowers/` is local, git-ignored working material; never
  link to it from the tracked set.

## Git

- Commit messages are conventional commits, `type(scope): summary`, e.g.
  `fix(graph): resolve a re-exported binding`. This repo carries no ticket keys.
- PRs squash-merge with that same title; the merged commit keeps its `(#N)` suffix.
- Never hand-edit a version. On merge to main, `.github/workflows/release.yml` runs `cz bump`,
  which drives `[project] version`, `auditor/__init__.py:__version__`, `CHANGELOG.md`, the commit
  and the tag, then publishes. There is no manual bump or tag push.
