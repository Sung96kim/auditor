<div align="center">
  <img src="assets/icon.svg" width="140" alt="auditor logo">

  # auditor

  Runs the mechanical half of a code audit (parse, class/function manifest, anti-pattern
  detectors, cached findings) over Python, TypeScript/React, shell, config and data files, and
  package manifests, for coding agents and CI.

  [![CI](https://github.com/Sung96kim/auditor/actions/workflows/ci.yml/badge.svg)](https://github.com/Sung96kim/auditor/actions/workflows/ci.yml)
  [![release](https://github.com/Sung96kim/auditor/actions/workflows/release.yml/badge.svg)](https://github.com/Sung96kim/auditor/actions/workflows/release.yml)
  [![PyPI](https://img.shields.io/pypi/v/auditr)](https://pypi.org/project/auditr/)
</div>

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
| `graph` | Semantic code graph: `build`, `serve`, `search`, `usages`, `flow`, `refine`, `eval`, `log`, and more. |
| `self` | Manage the install: `update`. |
| `version` | Print the installed version. |

## Setup

### Users

```bash
uv tool install "auditr[mcp]"     # CLI + MCP server on PATH
uvx auditr scan .                 # or run without installing
pipx install auditr               # or pipx, isolated and global
pip install "auditr[mcp,ts]"      # or into the active environment
```

- The PyPI distribution is `auditr`; the commands are `auditr` and `auditr-mcp`, with `auditor`
  and `auditor-mcp` as aliases.
- Extras gate features: `mcp` for the MCP server, `ts` for TypeScript/React (tree-sitter),
  `observer-claude` for `auditr graph refine` and `auditr graph eval`, `code-mode` for sandboxed
  tool orchestration.
- `observer-claude` pulls `claude-agent-sdk`, which bundles its own 342 MB `claude` binary.
  `observer` is the superset: it adds the Codex SDK and enables `graph refine` the same way.
- `observer-codex` pulls the Codex SDK and nothing imports it: `graph refine --runner codex` is
  refused. See [graph](docs/references/graph.md).
- `vectors` pulls `sqlite-vec` and `model2vec` and nothing reads it; `model2vec` also needs one
  online model fetch. See [configuration](docs/references/configuration.md).
- `graph` is an empty alias, kept so an existing `auditr[graph]` command or `uv tool` receipt keeps
  resolving; the graph libraries are core dependencies, about 175 MB of every install.
- <img src="assets/claude-color.svg" height="16" alt="Claude"> Claude Code plugin:
  `claude plugin marketplace add Sung96kim/auditor`, then `/plugin install auditor` in a session.
  See [claude-code-plugin](docs/references/claude-code-plugin.md).

### Developers

```bash
git clone https://github.com/Sung96kim/auditor && cd auditor
uv python install 3.13                        # the version CI pins
uv sync --extra dev --extra mcp --extra ts    # what CI installs
uv run auditr scan .                          # run the working tree
```

- Never `--all-extras`: it adds the `observer-*` and `vectors` SDK wheels, about 640 MB that nothing
  in the suite imports.
- `uv tool install .` puts the checkout on PATH.

### Containers

```bash
docker compose build                     # image with the mcp and ts extras baked in
docker compose run --rm auditor scan .   # the CLI against ./; set TARGET= for another repo
docker compose run --rm -T auditor-mcp   # the stdio MCP server
```

## Usage

### Users

```bash
auditr scan .                                  # severity counts and the worst files
auditr scan --vs-base --fail-on high           # only what changed, gating CI
auditr report src/service.py                   # one file, stateless: manifest plus findings
auditr aggregate . -o AUDIT.md                 # roll the incremental index up
auditr graph build . && auditr graph serve .   # build the semantic graph, then browse it
auditr-mcp                                     # stdio MCP server for agents (needs the mcp extra)
```

### Developers

```bash
uv run auditr scan .                           # any command above, from a source checkout
```

### Containers

```bash
docker compose run --rm auditor scan . --format sarif
```

- Every flag is on `--help`: `auditr <command> --help`. Every command has a page under
  [Docs](#docs).
- `-f json|sarif|md|html` picks the machine format and `-o` writes it to a file. Without `-f`,
  `scan` prints a human summary and `report` prints JSON.
- `--fail-on <severity>` exits 1 on any `auto` finding at or above it; `candidate` findings never
  gate. See [scan](docs/references/scan.md).

## Tests

### Local

```bash
uv run pytest -q                                                     # full suite, as CI runs it
uv run ruff check auditor plugin auditr_observer.py tests            # lint, as CI runs it
uv run ruff format --check auditor plugin auditr_observer.py tests   # format check, as CI runs it
```

### CI

```bash
uv run pytest tests/malware/test_integration.py -v   # the integration CI job; needs clamscan
```

- `.github/workflows/ci.yml` runs three jobs on every pull request:
  - `test`: the three local commands above.
  - `ui`: `pnpm install --frozen-lockfile`, `typecheck`, `test`, `build`, then a `git diff` that
    fails when the committed `auditor/graph/ui/dist/` is not what a rebuild produces.
  - `integration`: the malware suite against a real `clamscan`.

## Docs

### Orientation

| I want to | Page | Covers |
| --- | --- | --- |
| Understand how the pieces fit | [Architecture](docs/architecture.md) | Modules, seams, pipelines |
| Configure a repo or my account | [Configuration](docs/references/configuration.md) | Config files, env vars, defaults |

### Auditing

| I want to | Page | Covers |
| --- | --- | --- |
| Audit a repo or gate CI | [scan](docs/references/scan.md) | Scoping, filters, gating, baselines |
| Audit one file | [report](docs/references/report.md) | Stateless audit, JSON shape |
| See a file's structure | [manifest](docs/references/manifest.md) | AST class+function manifest |
| See what a scan would cover | [discover](docs/references/discover.md) | Discovery defaults, roles |
| Produce a repo-wide rollup | [aggregate](docs/references/aggregate.md) | Index rollup, AUDIT.md |
| Recompute repo-level findings | [crossfile](docs/references/crossfile.md) | Duplicates, dead code, cohesion |

### State, policy and rules

| I want to | Page | Covers |
| --- | --- | --- |
| Manage the shared cache | [index](docs/references/index.md) | Audit scope, partitions, pruning |
| Suppress a finding | [ignore](docs/references/ignore.md) | Ignore scopes, skip directives |
| See which config layer won | [config](docs/references/config.md) | Layering, profiles, validation |
| Set up the user config home | [init](docs/references/init.md) | `$AUDITOR_HOME`, overlays, checks |
| Look up a rule | [rules](docs/references/rules.md) | Rule ids, categories, verdicts |
| Add or debug a plugin | [plugins](docs/references/plugins.md) | Discovery, trust, contract |

### Graph and supply chain

| I want to | Page | Covers |
| --- | --- | --- |
| Query the semantic code graph | [graph](docs/references/graph.md) | Build, queries, flow, refinement |
| Run the background observer | [observer](docs/references/observer.md) | The daemon, its lifecycle, its API and the live page |
| Scan for malware and advisories | [malware](docs/references/malware.md) | ClamAV, osv-scanner, databases |

### Integrations

| I want to | Page | Covers |
| --- | --- | --- |
| Drive the auditor from an agent | [auditr-mcp](docs/references/auditr-mcp.md) | MCP tools, compact payloads |
| Use it inside Claude Code | [claude-code-plugin](docs/references/claude-code-plugin.md) | Skills, subagent, hooks |
| Call it from Python | [Python API](docs/references/python-api.md) | Entry points, models, index |
| Keep the install current | [self](docs/references/self.md) | `self update`, `version` |
