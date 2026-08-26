# Agent instructions

A deterministic code auditor. Three console scripts (`pyproject.toml` `[project.scripts]`):
`auditr` (CLI) and `auditr-mcp` (stdio MCP server), each with an `auditor`-prefixed alias, plus
`auditr-observer` (the observer client, a stdlib-only module at the repo root). `auditor/` is the
package, `plugin/` the Claude Code plugin, `tests/` mirrors the package.
[docs/architecture.md](docs/architecture.md) explains how the pieces fit; read it before
restructuring anything.

## Commands

```bash
uv python install 3.13                      # the version CI pins
uv sync --extra dev --extra mcp --extra ts   # what CI installs
uv run ruff check auditor plugin auditr_observer.py tests   # lint (CI)
uv run ruff format --check auditor plugin auditr_observer.py tests   # format check (CI); drop --check to rewrite
uv run pytest -q                            # full suite (CI)
uv run pytest tests/malware/test_integration.py -v   # CI integration job; needs clamscan on PATH
uv run auditr scan .                        # run the working tree, not an installed build
claude plugin validate ./plugin --strict    # plugin manifest check (CI runs it when available)
uv run cz bump --dry-run                    # preview the next version; release.yml runs the real bump + tag on merge to main
```

- Never `--all-extras` locally. After the graph libraries moved into `dependencies` that flag adds
  only the observer and vectors SDK wheels, about 640 MB that nothing in the suite imports.

The graph UI lives in `auditor/graph/ui` and uses pnpm only, never npm, npx, yarn, or bun:

```bash
cd auditor/graph/ui   # graph UI package root
pnpm install          # deps
pnpm typecheck        # tsc --noEmit
pnpm test             # vitest
pnpm build            # rebuild the committed dist/index.html that `graph serve` ships
```

## Layout rules

- `auditor/cli/` is one module per command; `cli/__init__.py` is the composition root that imports
  command modules for their registration side effects and mounts the sub-apps.
- `auditor/languages/<lang>/` holds a language auditor plus its detectors; `database/` the async
  SQLite index, `graph/` the semantic graph, `malware/`, `mcp/`, `reporters/`, `profiles/` their
  namesakes.
- `auditr_observer.py` sits at the repo root, outside the package, and imports nothing from
  `auditor` (importing it costs ~0.23 s and hooks run constantly). `auditor/observer/` holds the
  daemon side; the two share only the `OBSERVER_API_VERSION` literal, pinned by a test.
- Shared seams stay at the `auditor/` top level (`engine.py`, `config.py`, `models.py`,
  `registry.py`); never bury one inside a feature package.
- `plugin/` is the Claude Code plugin (skills, agents, hooks, statusline). Its Python is
  stdlib-only and imports nothing from `auditor`; it drives the installed `auditr` CLI.
- `plugin/statusline/auditor_status.py` re-implements four package helpers by hand, so it must
  stay in sync with `discovery.find_root`, `paths.repo_identity`, `paths.repo_dir_key` and
  `paths.auditor_home`. `tests/plugin/test_statusline.py` pins each pair; change one side and
  change the other.
- `tests/` mirrors the package; shared helpers live in `tests/_support.py`, fixture repos in
  `tests/fixtures/`, which ruff and pytest collection both exclude because its anti-patterns are
  intentional.

## Code conventions

- Imports at module top, never inside a function; `tests/test_dogfood.py` fails on an inline import
  in the package.
- One sanctioned exception to the import rule: `auditor/cli/lazy.py` defers the `graph` sub-app's
  import so the fast commands never load numpy/scikit-learn/networkx. It carries the scoped
  directive; do not add a second deferred import anywhere.
- Everything is typed. Records are pydantic v2 models: `ConfigDict(frozen=True)` on `Finding`,
  `ManifestEntry`, `SkippedRule`; `ScanResult`, `IndexEntry`, `ConfigNotice`, `StagedRun`,
  `RunRegistry`, `BoundTools` and `Conversation` are mutable aggregates. Configuration is `pydantic-settings`.
- Detectors, language auditors, and reporters register by subclassing their ABC, so a package
  `__init__.py` imports its modules purely for that side effect (F401 is waived there).
- Suppress a finding you have judged in source: `# auditor: skip: RULE-ID` on the offending line,
  or `# auditor: skip-file: RULE-ID`, each with a short parenthetical reason.
- Comment blocks stay under the `PY-STYLE-LONG-COMMENT` floor (3 prose lines) and only where the
  code is not self-evident.

## Testing conventions

- `asyncio_mode = "auto"`: async tests and fixtures need no marker.
- The autouse `_isolated_auditor_home` fixture in `tests/conftest.py` repoints `AUDITOR_HOME` at a
  throwaway dir, so no test touches the real shared index. Do not opt out of it.
- Reuse the shared scaffolding: the `sample_repo` fixture (writable copy of the sample repo) and
  `run_audit` / `run_ts_audit` / `run_sh_audit` in `tests/_support.py`.
- Function-and-fixture style, `test_*.py` files, `test_` prefixed functions; parametrize over
  near-identical cases.
- `tests/test_dogfood.py` scans `auditor/` itself and asserts three things: no
  `PY-STYLE-INLINE-IMPORT`, no `PY-STYLE-IF-FALSE-IMPORT`, and no `malware`, `secrets` or
  `security` finding anywhere in the package (any of those would be a detector false positive).
  Nothing else gates on that scan: judged findings carry a skip directive with a reason, and CI
  runs no `auditr scan` of the package, so the remaining candidates do not fail the build.
- New production code ships with tests; a bug fix ships with a regression test that fails without
  the fix.

## Docs

- A feature change ships with its doc update in the same change: the `docs/references/*.md` page it
  touches, `README.md` when the front page changes, and `docs/architecture.md` when the module
  layout or a pipeline changes.

## Git

- Commit messages are conventional commits, `type(scope): summary`, e.g.
  `fix(graph): resolve a re-exported binding`. This repo carries no ticket keys.
- PRs squash-merge with that same title; the merged commit keeps its `(#N)` suffix.
- Never hand-edit a version. On merge to main, `.github/workflows/release.yml` runs `cz bump`,
  which drives `[project] version`, `auditor/__init__.py:__version__`, `CHANGELOG.md`, the commit
  and the tag, then publishes. There is no manual bump or tag push.
