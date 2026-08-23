# Configuration reference

Committed config lives in `[tool.auditor]` in `pyproject.toml` or in `.auditor/config.toml`;
env-driven config lives in `auditor/config.py` (`GlobalPaths`, plus `AUDITOR_*` overrides of
`AuditorSettings`). This page covers both.

- How a value is resolved across those sources is in [config.md](config.md), which is also the
  command that prints the merged result.
- An unknown key is ignored instead of failing the load, so a key a newer auditor understands does
  not break an older install on the same repo. Each command prints the ignored keys once, on
  stderr, so machine output on stdout stays parseable; `auditr config check` lists them with their
  dotted path.
- A key with an invalid value still fails: the command prints one line and exits non-zero.

## `[tool.auditor]` in `pyproject.toml`

Table names are prefixed with `tool.auditor`. Sub-tables map one-to-one onto the models in
`auditor/config.py`.

```toml
[tool.auditor]
extends = "strict"
exclude = ["vendor/**", "legacy/**"]
diff_base = "origin/main"

[tool.auditor.rules]
PY-TYPING-MISSING-HINTS = { severity = "high" }
PY-OOP-CONSTRUCTOR-WALL = { enabled = true, threshold = { oop = { wall_kwarg_min = 10 } } }

[tool.auditor.categories]
security = { min_severity = "high" }
```

### Top level (`AuditorSettings`)

- `extends` (default `"base"`): profile chain root. A built-in name (`base`, `strict`, `pydantic`,
  `all-strict`) or a path to a TOML file.
- `exclude` (default `[]`): extra globs to skip, added to the built-in defaults.
- `resolve_packages` (default `[]`): dotted-name prefixes of dependency packages whose installed
  source the callee resolver may read from the scanned project's environment. Repo-local
  resolution always works; this extends it. Set with no environment found, the scan warns and
  dependency resolution stays off.
- `respect_gitignore` (default `true`): skip git-ignored files.
- `rules` (default `{}`): per-rule overrides, keyed by rule id. Validated against the registry, so
  an unknown id fails the load.
- `categories` (default `{}`): per-category overrides, keyed by category name. Validated against
  the registered categories.
- `roles` (default `{}`): per-role policy, keyed by `production`, `test`, `test_support`, `script`,
  `generated`.
- `role_globs` (default `{}`): role to globs. Checked before the built-in path/content
  classification; a glob matches the repo-relative path or the bare filename.
- `test_mode` (default unset): forces `relaxed`, `strict` or `excluded` for `test` and
  `test_support` files, overriding their role policy.
- `overrides` (default `[]`): per-glob or per-role overrides, applied after role policy.
- `plugins` (default `[]`): importable module paths loaded before validation, so config may
  reference plugin-contributed rules. No trust gate ([plugins.md](plugins.md)).
- `trust_local_plugins` (default `false`): load `.auditor/plugins/*.py`, which execute code.
- `respect_skips` (default `true`): honor in-file `auditor: skip` directives.
- `observer_allowed` (default `true`): the repo's hard opt-out for the graph observer. Set `false`
  and no observer attaches to this checkout, whatever the user's own settings say. Today it is
  only a field: the gate that ANDs it with the user's `observer.enabled` ships with the observer
  daemon, so setting it now has no effect until then.
- `settings_modules` (default `["config", "settings"]`): module stems or directory names that are a
  blessed home for `BaseSettings` subclasses (`PY-CONFIG-SCATTERED-SETTINGS`).
- `settings_cohesion` (default `true`): also bless the de-facto home, the module where settings
  classes already cluster.
- `lint_overlap` (bool, default `false`): accepted and currently unused; no code reads it.
- `cli_frameworks` (default `["typer", "click"]`): CLI frameworks whose free-function-command idiom
  exempts a module from the OOP orchestrator and cross-file duplicate-function heuristics. Extend
  it for an in-house framework.
- `diff_base` (default unset): the ref `scan --vs-base` diffs against. Unset auto-detects `main`,
  `master`, `develop`, `development`.

### Thresholds (`[tool.auditor.threshold]`, `Threshold`)

Every floor is an integer with `ge=1`. A partial override deep-merges onto the base, so tuning one
floor keeps the rest.

- `threshold.oop` (`OopThreshold`): `wall_kwarg_min` 12, `flat_field_min` 10, `field_copy_min` 4,
  `module_const_min` 2, `dispatch_min_branches` 5, `cli_logic_min_calls` 3.
- `threshold.size` (`SizeThreshold`): `file_max_lines` 800, `max_params` 6, `max_methods` 20,
  `max_attrs` 15, `max_complexity` 10, `comment_block_max_lines` 3.
- `threshold.dry` (`DryThreshold`): `dup_block_min_statements` 3, `dup_block_min_tokens` 12,
  `parallel_sibling_min_tokens` 4, `parallel_sibling_min_group` 2, `xfile_method_min_statements` 3.
- `threshold.jsx` (`JsxThreshold`): `max_jsx_depth` 6, `repeated_jsx_min` 3,
  `repeated_jsx_min_tags` 2.
- `threshold.test` (`TestThreshold`): `parametrize_min_clones` 3, `parametrize_min_statements` 2,
  `setup_min_statements` 2, `setup_min_tests` 3, `max_mocks_per_test` 4.
- Each `Field` carries its own description; `auditr config show` prints the resolved values.

### Rules (`[tool.auditor.rules]`, `RuleConfig`)

All four keys are optional; unset means inherit from the detector or the layer below.

- `enabled`: turn one rule off or on.
- `severity`: `blocking`, `high`, `medium`, `low` or `suggestion`.
- `verdict_kind`: `auto` or `candidate`.
- `threshold`: a partial `Threshold`, deep-merged onto the global one for this rule only. Changing
  it re-runs only that rule on the next incremental scan, because the cache keys each rule by
  content plus its resolved config.
- `auditr rules list` prints the ids ([rules.md](rules.md)).

### Categories (`[tool.auditor.categories]`, `CategoryConfig`)

- `enabled`: turn a whole category off.
- `min_severity`: a floor, not a cap. It raises a rule below it and never lowers one above it.

### Roles (`[tool.auditor.roles]`, `RolePolicy`)

- `mode` (default `"strict"` when a policy is declared): `relaxed` applies that role's declared
  `rules` and `categories`, `strict` ignores them and audits at full production strength,
  `excluded` skips the file.
- `rules`, `categories` (default `{}`): same shapes as the top-level tables, scoped to the role and
  applied only in `relaxed` mode.
- With no policy declared for a role, the built-in default is `relaxed` for `test` and
  `test_support`, `excluded` for `generated`, `strict` otherwise. The shipped profiles declare
  these explicitly.

### Per-glob overrides (`[[tool.auditor.overrides]]`, `OverrideConfig`)

Applied last, after role policy, in declaration order (later entries win). This is the ruff
`per-file-ignores` model.

```toml
[[tool.auditor.overrides]]
path = "app/legacy/**"
rules = { PY-STYLE-FILE-SIZE = { enabled = false } }
```

- `path` (default unset): fnmatch glob against the repo-relative path.
- `role` (default unset): restrict the entry to one role.
- `rules`, `categories` (default `{}`): the overrides to apply on a match.
- An entry with neither `path` nor `role` never matches. With both set, both must match.

### Design system (`[tool.auditor.design_system]`, `DesignSystem`)

The `design-system` TypeScript rules are dormant until a repo declares its own vocabulary; the
auditor hardcodes no component names.

```toml
[tool.auditor.design_system]
ui_paths = ["components/ui"]
shell = "@/components/shell"

[[tool.auditor.design_system.primitives]]
component = "Badge"
when_class = "rounded-full.*text-xs"
size_override = true
```

- `ui_paths` (default `[]`): import paths that bypass the shell. Non-empty activates
  `TS-DS-DIRECT-UI-IMPORT`; files under these paths are exempt from it.
- `shell` (default unset): the entrypoint named in that finding's message.
- `primitives` (default `[]`): list of `DesignSystemPrimitive`.
  - `component` (required): the primitive to recommend.
  - `when_class` (default unset): regex over `className`. Set, it activates
    `TS-DS-INLINE-PRIMITIVE` for that primitive.
  - `requires_text` (default `true`): only flag elements that render a text child, skipping
    icon-only backdrops.
  - `size_override` (default `false`): also flag fixed `h-`, `w-` or `size-` classes on the
    component (`TS-DS-SIZE-OVERRIDE`).

### SQLAlchemy (`[tool.auditor.sqlalchemy]`, `SqlAlchemyConfig`)

Facts about the session factory the auditor cannot see from a model file. Each one activates a rule
that is otherwise dormant.

- `expire_on_commit` (default `false`): set `true` to activate `SA-GREENLET-ATTR-AFTER-COMMIT`.
- `async_session` (default `false`): set `true` to activate `SA-IMPLICIT-LAZY-ASYNC`.

### Semantic graph (`[tool.auditor.graph]`, `GraphConfig`)

Opt-in per repo, but no extra to install: the graph libraries ship in the core distribution
([graph.md](graph.md)).

- `enabled` (default `false`): make a plain `scan -i` populate graph facts. `auditr graph build`
  extracts them regardless.
- `detect` (default `true`): run the graph-native detectors during a build.
- `name_similarity_threshold` (default `0.45`, 0 to 1): minimum name similarity for a semantic
  edge.
- `knn_k` (default `8`, `ge=1`): neighbors kept per node for name and usage edges.
- `cluster_floor` (default `0.45`, 0 to 1): minimum edge weight for concept clustering.
- `stopwords` (default `[]`): repo-specific tokens dropped on top of the english and structural
  lists.
- `god_concept_sigma` (default `3.0`, `ge=0`): standard deviations above the mean before a concept
  is a hub.
- `scattered_min_modules` (default `5`, `ge=1`) and `scattered_min_ratio` (default `0.5`, 0 to 1):
  modules and module-to-member ratio before a concept counts as scattered.
- `naming_verb_distance` (default `0.15`, `ge=0`), `naming_object_jaccard` (default `0.6`, 0 to 1),
  `naming_min_verb_count` (default `20`, `ge=1`): thresholds for the naming-inconsistency detector.

### Malware scan (`[tool.auditor.malware_scan]`, `MalwareScanConfig`)

Opt-in shell-outs to ClamAV and osv-scanner ([malware.md](malware.md)). No pip extra; the backends
are system binaries.

- `enabled` (default `false`): run the scan as part of `scan`. `scan --malware` is the per-run
  equivalent.
- `content` (default `true`): the ClamAV pass over file contents.
- `dependencies` (default `true`): the osv-scanner pass over lockfiles.
- `include_vendored` (default `true`): scan `node_modules`, `.venv` and `vendor`, where payloads
  live.
- `max_file_size_mb` (default `50`, `ge=1`): skip files larger than this.
- `include_vulnerabilities` (default `false`): also report CVEs, not just `MAL-*` advisories.
- `scan_timeout_s` (default `600`, `ge=1`): per-scan timeout.

## `.auditor/config.toml`

Same schema, same defaults, without the `tool.auditor` prefix: `extends` at the top level,
`[rules]`, `[threshold.size]`, `[[overrides]]`. Use it in a repo with no `pyproject.toml`, or to
keep auditor config out of it.

```toml
extends = "strict"

[threshold.size]
max_complexity = 12
```

- It is the higher layer: on a key both files set, `.auditor/config.toml` wins.

## Overriding for one run

```bash
# swap the profile without editing config
auditr scan . --profile all-strict

# add ignore globs on top of the config's exclude (repeatable)
auditr scan . --exclude 'legacy/**' --exclude 'vendor/**'

# merge a JSON object over the whole config as the highest layer
auditr scan . --config-json '{"sqlalchemy":{"expire_on_commit":true}}'
```

- `--strict-tests` sets `test_mode = "strict"`, `--no-skips` sets `respect_skips = false`, and
  `--include-gitignored` sets `respect_gitignore = false` for that run.
- `--allow-local-plugins` loads `.auditor/plugins/*.py` for that run. It does not set
  `trust_local_plugins`, so the resolved config still reports the field's own value.
- `--exclude` appends to the config's `exclude`; `--config-json` deep-merges over everything else.
- `config show --config-json` accepts the same object, so an override can be checked before it is
  used ([scan.md](scan.md), [config.md](config.md)).

## Profiles

Built-in profiles live in `auditor/profiles/`. Each may itself set `extends`, so they form a chain
resolved before any repo file is read; a cycle is an error.

- `base`: the industry floor. Security, malware, secrets, supply-chain, correctness, async, typing,
  config and cross-file dedup are on; the `oop-composition` category is off except five
  suggestion-tier rules re-enabled by name. `test` and `test_support` are `relaxed` with `typing`
  and `oop-composition` off and the noise-by-design security rules disabled or downgraded to
  `candidate`; `script` is `relaxed`; `generated` is `excluded`.
- `strict`: extends `base` and turns the `oop-composition` category back on.
- `pydantic`: extends `strict` and adds no overrides of its own. Same ruleset as `strict`, named so
  the config self-documents a Pydantic-first codebase. The Pydantic-aware rules are gated on the
  project's dependencies, not on this profile.
- `all-strict`: extends `strict` and flips `roles.test`, `roles.test_support` and `roles.script` to
  `mode = "strict"`, so no role keeps a relaxed carve-out.
- A path works in place of a name: `extends = "profiles/house.toml"` loads that file and resolves
  its own `extends`.

## The `.auditor/` directory

- `config.toml`: repo config, described above. Committed.
- `plugins/*.py`: local detector modules. They execute code, so they load only under the
  `trust_local_plugins` field above, or `--allow-local-plugins` for one run. Git-ignored in this
  repo.
- `baseline.json`: the conventional path for `scan --write-baseline` and `--baseline`. Nothing
  reads it unless the path is passed. Commit it to adopt the tool on a legacy repo.
- Nothing else. The status cache moved to `$AUDITOR_HOME/repos/<repo_dir_key>/status.json`; an
  older `.auditor/.status.json` is ignored, and `auditr init --clean-status` deletes it.

Generated state does not live here. The incremental index, persistent ignores and graph share one
database under `$AUDITOR_HOME`.

## User settings (`$AUDITOR_HOME`)

Personal settings are never committed. They live under `$AUDITOR_HOME` (default `~/.auditor`),
are created by [`auditr init`](init.md), and are modelled by `UserSettings` in
`auditor/user_settings.py`.

```
$AUDITOR_HOME/
  config.json              # global user settings
  config.schema.json       # generated from the models, for editor completion
  index.db                 # the shared index
  models/                  # cache for the optional vector layer
  repos/<repo_dir_key>/    # one dir per repo, keyed by sha1 of the resolved git common dir
    root.json              # breadcrumb {root, identity, created_at}
    config.json            # per-repo personal overrides
    status.json            # the status line's cache
    status.lock
```

- The layout grows with the tool. Later releases add `repos/<repo_dir_key>/spool.jsonl` and an
  `observer/` directory for the background observer's lock, logs and state. Nothing above is
  created before `auditr init` or a scan needs it.
- `repo_dir_key` is the sha1 of `git rev-parse --path-format=absolute --git-common-dir`, resolved,
  falling back to the resolved root outside git. Every worktree of one checkout shares the
  directory, and a symlinked path does not mint a second one.
- Layers for user keys, later wins: defaults in the models, then `$AUDITOR_HOME/config.json`, then
  `$AUDITOR_HOME/repos/<key>/config.json`, then `AUDITOR_USER_*`. CLI flags stay above all of it.
- The two models never share a key. Rule, threshold, exclude, role and `diff_base` keys exist only
  on `AuditorSettings`; `observer` and `vectors` only on `UserSettings`.
- An unknown key is ignored and reported on stderr; `auditr config check` lists them with their
  dotted path.

### `observer` (`ObserverConfig`)

- `enabled` (default `true`): attach the observer to auditor-configured repos.
- `runner` (default `"auto"`): `auto`, `claude` or `codex`.
- `model` (default `"haiku"`): `haiku` or `sonnet`, the Claude tier a refinement run uses.
- `codex_model` (default `""`): Codex model override; empty uses the user's Codex default.
- `min_precision` (default `0.95`, 0 to 1): measured precision a kind needs before going active.
- `max_cost_usd_per_day` (default `2.0`), `max_runs_per_day` (default `40`),
  `max_budget_usd_per_run` (default `0.25`): the spend and run ceilings.
- `max_turns` (default `20`), `max_nodes_per_run` (default `12`), `max_changes_per_run`
  (default `25`): per-run size limits.
- `max_utilization` (default `0.5`, 0 to 1): share of the rate-limit window the observer may take.
- `min_new_unresolved` (default `1`): new unresolved callees an edit batch needs to earn a run.
- `run_on_stale` (default `true`): re-run when an edit stales an existing refinement.
- `low_budget_fraction` (default `0.25`, 0 to 1): remaining daily budget below which only
  high-value runs proceed.
- `debounce_seconds` (default `20`), `session_expiry_minutes` (default `45`),
  `idle_shutdown_minutes` (default `30`): the daemon's timing.
- `skipped_retention_days` (default `7`): days of skipped-run history kept.
- `worktrees` (default `"main"`): `main` or `all`.
- `suspects` (default `true`): queue suspect nodes found during a build.
- `tuning` (default `"propose"`): `propose` or `off`.
- `stopwords_max` (default `20`): most repo-specific stopwords a tuning proposal may add.
- `open_browser` (default `true`): open the live page when the daemon starts.
- `codex_prices` (default `{}`): model to `{input, output}` in USD per million tokens. Empty uses
  the shipped table.

### `vectors` (`VectorsConfig`)

- `enabled` (default `false`): enable the opt-in `sqlite-vec` plus static-embedding layer.
- `model` (default `"minishlab/potion-base-8M@bf8b056"`): the pinned model and revision.

### User environment variables

| Form | Example | Notes |
| --- | --- | --- |
| Nested table | `AUDITOR_USER_OBSERVER='{"model":"sonnet"}'` | JSON value, merged over both files. |
| Scalar field | `AUDITOR_USER_CONFIG_VERSION=1` | Field name uppercased. |

- `AUDITOR_OBSERVER=0` is not a settings field. It is the kill switch the plugin hooks and the
  daemon read straight from the environment, which is why user settings use their own
  `AUDITOR_USER_` prefix.

## Environment variables

### Global paths (`GlobalPaths`)

| Var | Default | Purpose |
| --- | --- | --- |
| `AUDITOR_HOME` | `~/.auditor` | Root of all generated global state: `index.db` (the shared index, partitioned by repo, holding cached findings, persistent ignores and graph facts), `bin/` (the checksum-verified osv-scanner download), `osv-db/` (the OSV database). |
| `AUDITOR_CODE_MODE` | unset (`false`) | Enables the experimental Code Mode transform on the MCP server. A no-op unless the `code-mode` extra is installed ([auditr-mcp.md](auditr-mcp.md)). |

### Repo settings (`AuditorSettings`)

Every field above is also settable from the environment under the same `AUDITOR_` prefix.

| Form | Example | Notes |
| --- | --- | --- |
| Scalar field | `AUDITOR_RESPECT_GITIGNORE=false` | Field name uppercased. |
| List or model field | `AUDITOR_SQLALCHEMY='{"expire_on_commit":true}'` | JSON value, parsed and validated like a TOML table. |

- The environment is the lowest layer. It is deep-merged under the TOML layers, so an `AUDITOR_*`
  value only reaches keys no profile or repo file sets.
- Policy keys are not settable from the environment at all: `rules`, `categories`, `threshold`,
  `exclude`, `overrides`, `roles`, `role_globs`, `respect_skips` and `diff_base` are stripped from
  the env source, so a shell variable cannot disable a rule the repo leaves unmentioned. Put them
  in TOML, or pass `--config-json` for one run.
- `AUDITOR_EXTENDS` never applies: the loader always writes `extends` into the merged config. Use
  `extends` in TOML or `scan --profile`.
- Personal settings live under a different prefix entirely, `AUDITOR_USER_*`. See
  [User settings](#user-settings-auditor_home).

### Claude Code plugin hooks

Read directly by `plugin/hooks/*.py`, not by any settings class
([claude-code-plugin.md](claude-code-plugin.md)).

| Var | Default | Purpose |
| --- | --- | --- |
| `AUDITOR_AUTOHOOK` | unset (on) | Set to `0` to disable the PostToolUse hook that audits each edited file. |
| `AUDITOR_AUTOHOOK_ASYNC` | unset (sync) | Set to `1` to detach a background incremental repo scan instead of running a single-file `auditr report` in-turn. |
| `AUDITOR_AUTOHOOK_SEVERITY` | `high` | Severity floor for findings the hook reports inline. |
| `AUDITOR_VERIFY_HOOK` | unset (off) | Set to `1` to enable the Stop hook that gates finishing on `scan --since HEAD`. |
| `AUDITOR_VERIFY_SEVERITY` | `high` | Severity floor that trips that gate. |
