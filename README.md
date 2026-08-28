# auditor

Runs the mechanical half of a code audit (parse, class/function manifest, anti-pattern detectors,
cached findings) over Python, TypeScript/React, shell, config and data files, and package
manifests, for coding agents and CI.

| Command | What it does |
| --- | --- |
| `scan` | Audit a file or directory: filter, scope to a diff, gate CI. |
| `report` | Audit one file statelessly: manifest plus findings. |
| `manifest` | Print one Python file's AST class+function manifest. |
| `discover` | List auditable files with their classified role. |
| `aggregate` | Roll the incremental index up into `AUDIT.md`. |
| `crossfile` | Recompute cross-file duplicate findings from the index. |
| `index` | Audit scope and cache: `add`, `list`, `repos`, `forget`. |
| `ignore` | Persistent finding ignores: `add`, `list`, `rm`, `clear`. |
| `config` | Resolved configuration: `show`, `check`. |
| `init` | Create the user config home under `$AUDITOR_HOME`. |
| `rules` | Detector registry: `list`. |
| `plugins` | Loaded detectors, languages, reporters: `list`. |
| `malware` | Opt-in ClamAV and osv-scanner backends: `status`, `update-dbs`, `install`. |
| `graph` | Semantic code graph: `build`, `serve`, `search`, `usages`, `related`, `neighbors`, `concept`, `clusters`, `export`. |
| `self` | Manage the install: `update`. |
| `version` | Print the installed version. |

## Setup

```bash
# install the CLI on PATH from PyPI
uv tool install "auditr[mcp]"
# install from a checkout
uv tool install .
# install from GitHub
uv tool install git+https://github.com/Sung96kim/auditor
# or with pip, into the active environment
pip install "auditr[mcp,ts]"
# or with pipx, isolated and global like uv tool
pipx install auditr
# run it without installing anything
uvx auditr scan .
# no local Python; the image bundles the mcp and ts extras
docker compose run --rm auditor scan .
```

- The PyPI distribution is `auditr`; the commands are `auditr` and `auditr-mcp`, with `auditor`
  and `auditor-mcp` as aliases.
- `auditr-observer` installs alongside them as a placeholder until the observer ships: every
  subcommand prints that it is not available in this release and exits 0.
- Extras gate features: `mcp` for the MCP server, `ts` for TypeScript/React (tree-sitter),
  `code-mode` for sandboxed tool orchestration.
- The semantic graph is part of the core install. `graph` survives as an empty extra so existing
  `auditr[graph]` install commands and `uv tool` receipts keep resolving. It costs every install
  about 175 MB: scipy 91 MB (a transitive dependency of scikit-learn), scikit-learn 36 MB,
  numpy 33 MB, networkx 13 MB, snowballstemmer 2 MB.
- <img src="assets/claude-color.svg" height="16" alt="Claude"> `observer-claude` is what `auditr
  graph refine` needs: it pulls `claude-agent-sdk`, which bundles its own 342 MB `claude` binary.
  Install it with `pip install "auditr[observer-claude]"` only if you want model-driven
  refinement; every other command works without it.
- `observer` is the superset and enables `auditr graph refine` the same way `observer-claude`
  does, since it pulls `claude-agent-sdk` too.
- <img src="assets/codex-color.svg" height="16" alt="Codex"> `observer-codex` and `vectors` are
  declared and nothing uses them yet. Each pulls a large SDK, so leave them out until a release
  says otherwise.
- <img src="assets/claude-color.svg" height="16" alt="Claude"> Claude Code plugin:
  `claude plugin marketplace add Sung96kim/auditor`, then `/plugin install auditor` in a session.
  See [claude-code-plugin](docs/references/claude-code-plugin.md).

## Usage

```bash
# audit a directory: severity counts and the worst files
auditr scan .
# audit one file, stateless: manifest plus findings
auditr report src/service.py
# AST manifest only, no detectors
auditr manifest src/service.py
# list auditable files with their classified role
auditr discover .
# roll the incremental index up into AUDIT.md
auditr aggregate . -o AUDIT.md
# recompute cross-file duplicates from the index
auditr crossfile .
# register files as the audit scope
auditr index add src/service.py
# mute one rule in one file, stored in the shared index
auditr ignore add PY-SEC-WEAK-HASH --file src/legacy.py
# print the resolved configuration
auditr config show
# create the user config home ($AUDITOR_HOME) and this repo's overlay
auditr init --repo
# list the detector rules in one category
auditr rules list --category security
# show every loaded plugin and where it came from
auditr plugins list
# check the opt-in malware backends and their databases
auditr malware status
# build the semantic graph
auditr graph build .
# the corrections recorded for this checkout
auditr graph refinements list
# let a model work the unresolved queue under a path
auditr graph refine auditor/cli
# measure this runner before it may activate corrections (--dry-run costs nothing)
auditr graph eval --suite all
# check PyPI for a newer release and install it
auditr self update
# print the installed version
auditr version
# stdio MCP server for agents (needs the mcp extra)
auditr-mcp
```

- `-f json|sarif|md|html` picks the machine output format and `-o` writes it to a file; without
  `-f`, `scan` prints a human summary and `report` prints JSON.
- `--fail-on <severity>` exits 1 on any `auto` finding at or above it; `candidate` findings never
  gate. See [scan](docs/references/scan.md).

## Tests

```bash
# deps for the whole suite; never --all-extras, the observer and vectors SDKs are ~640MB
uv sync --extra dev --extra mcp --extra ts
# the tests the CI test job runs (.github/workflows/ci.yml); that job also lints
uv run pytest -q
```

## Docs

- [Architecture](docs/architecture.md): modules, pipeline, shared seams.
- [Configuration](docs/references/configuration.md): config files, env vars, thresholds.
- [scan](docs/references/scan.md): scoping, filters, CI gating.
- [report](docs/references/report.md): single-file stateless audit.
- [manifest](docs/references/manifest.md): AST class+function manifest.
- [aggregate](docs/references/aggregate.md): index rollup, AUDIT.md.
- [crossfile](docs/references/crossfile.md): cross-file duplicate findings.
- [discover](docs/references/discover.md): discovery defaults, role classification.
- [index](docs/references/index.md): audit scope, shared cache.
- [ignore](docs/references/ignore.md): persistent ignore scopes.
- [config](docs/references/config.md): resolved configuration layers.
- [init](docs/references/init.md): user config home, per-repo overlay, checks.
- [rules](docs/references/rules.md): rule registry, category filters.
- [plugins](docs/references/plugins.md): plugin discovery, load sources.
- [malware](docs/references/malware.md): ClamAV, osv-scanner, databases.
- [graph](docs/references/graph.md): graph build, queries, UI.
- [self](docs/references/self.md): in-place install updates and `version`.
- [auditr-mcp](docs/references/auditr-mcp.md): MCP tools, compact payloads.
- [claude-code-plugin](docs/references/claude-code-plugin.md): skills, subagent, hooks.
- [Python API](docs/references/python-api.md): library entry points, exported models.
