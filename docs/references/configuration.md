# Configuration reference

Committed config lives in `[tool.auditor]` in `pyproject.toml` or in `.auditor/config.toml`;
env-driven config lives in `auditor/config.py` (`GlobalPaths`, plus the one `AUDITOR_*` override
`AuditorSettings` accepts). This page covers both.

- How a value is resolved across those sources is in [config.md](config.md), which is also the
  command that prints the merged result.
- An unknown key is ignored instead of failing the load, so a key a newer auditor understands does
  not break an older install on the same repo.
- The CLI prints the ignored keys once per invocation, from the root callback, on stderr, so
  machine output on stdout stays parseable. Both families are covered: repo policy and user
  settings. `auditr config check` and `auditr init` print their own list instead.
- The MCP server prints one line naming them on stderr, once per repo it is asked about. It reads
  the repo from a call's `path`, `file` or `root` argument, or from that argument's own default
  when the call leaves it out.
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
- `exclude` (default `[]`): extra globs to skip, added to the built-in defaults. Those
  defaults are the generated-file patterns (`*.gen.py`, `*_pb2.py`, `*.gen.ts`, `*.d.ts` and
  friends) plus `.claude/worktrees/*`: an agent worktree is a second checkout of the same
  repo, so scanning it double counts every finding in it.
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
- `observer_allowed` (default `true`): the repo's hard opt-out for the graph observer. It is the
  third clause of the daemon's attach gate, so `false` refuses every session in this repo.
- `settings_modules` (default `["config", "settings"]`): module stems or directory names that are a
  blessed home for `BaseSettings` subclasses (`PY-CONFIG-SCATTERED-SETTINGS`).
- `settings_cohesion` (default `true`): also bless the de-facto home, the module where settings
  classes already cluster.
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
- `flow_hub_fan_in` (default `40`, `ge=1`): the fan at which `auditr graph flow` collapses a node
  instead of expanding it. Two counts are compared against it: how many symbols reach the node
  (dispatch children included) and how many children it would emit. Either one crossing makes it a
  hub, which is why a helper called from 90 places collapses even though it calls one thing.
  `--expand-hubs` ignores the floor for one run.
- `refine_cluster_jaccard` (default `0.5`, 0 to 1): how much of a cluster refinement's recorded
  member set must still be in one cluster for it to re-attach. Below the floor the refinement goes
  `stale`.
- `refine_max_noop_builds` (default `3`, `ge=1`): consecutive builds a refinement may have no
  effect on before it goes `stale`. A `pinned` refinement counts but never expires.
- `rebuild_lock_poll_seconds` (default `0.25`, `gt=0`): how often a build blocked on another
  process's rebuild lock retries.
- `rebuild_lock_timeout_seconds` (default `120.0`, `gt=0`): how long a caller that will not wait
  for ever gives another process's build before it refuses. Read by `graph_refine_commit`,
  `RefinementService.rebuild` and the MCP `graph_build` tool; `auditr graph build` waits instead.

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
  observer/                # the daemon's own state
    lock                   # the singleton flock; whoever holds it is the daemon
    daemon.json            # {pid, port, home, version, compat}
    log/observer.log       # the daemon's rotating log
    locks/                 # the graph rebuild lock's, not the daemon's
  repos/<repo_dir_key>/    # one dir per repo, keyed by sha1 of the resolved git common dir
    root.json              # breadcrumb {root, identity, created_at}
    config.json            # per-repo personal overrides
    spool.jsonl            # the observer's accepted, unconsumed events for this repo
    status.json            # the status line's cache
    status.lock
```

- Nothing above is created before `auditr init` or a scan needs it. `observer/locks/` appears the
  first time a graph build takes its rebuild lock, and the daemon adds only `lock`, `daemon.json`
  and `log/` beside it; it never creates or clears `observer/` itself.
- `repo_dir_key` is the sha1 of `git rev-parse --path-format=absolute --git-common-dir`, resolved,
  falling back to the resolved root outside git. Every worktree of one checkout shares the
  directory, and a symlinked path does not mint a second one.
- Layers for user keys, later wins: defaults in the models, then `$AUDITOR_HOME/config.json`, then
  `$AUDITOR_HOME/repos/<key>/config.json`, then `AUDITOR_USER_*`. CLI flags stay above all of it.
- The two models never share a key. Rule, threshold, exclude, role and `diff_base` keys exist only
  on `AuditorSettings`; `observer` and `vectors` only on `UserSettings`.
- An unknown key is ignored and reported on stderr; `auditr config check` lists them with their
  dotted path.
- `config_version` is `2`. Version 2 grouped twenty flat `observer` keys into five sub-tables, so a
  file written by an older release is reported on one stderr line naming every key it holds and
  where each one moved to. Those keys are reported as moves, never as typos, and nothing is
  migrated for you: the values behind them are not read until you move them.
- `auditr init` refuses to stamp version 2 on a file that still holds the old keys. Move them, or
  pass `--force` to stamp the version and leave the keys where they are.

### `observer` (`ObserverConfig`)

Five keys sit at the top of the table; the rest live in five sub-tables. `auditr graph refine`,
`auditr graph eval` and `auditr graph refinements prune` read the budget, limits and tuning keys
called out below. The rest of the table is the observer daemon's own, read by the daemon
`auditr observer start` runs: see [observer.md](observer.md).

- `enabled` (default `true`) and `worktrees` (default `"main"`, or `"all"`) are two clauses of the
  attach gate: with `enabled` false no repo attaches, and under `"main"` a linked worktree is
  refused. `suspects` (default `true`) turns on the loop's suspect drain, spec 8.3's item 3; false
  skips it, so nothing but an edit batch or a verify pass opens a run. `open_browser` (default
  `true`) opens the daemon's page once per daemon lifetime, on the first session that attaches.

- `skipped_retention_days` (default `7`): days of assessment-row history kept. Only the gate's
  own rows are swept; an evicted or stranded run is `skipped` too and is kept whatever its age.
  `0` is legal and means the next sweep reaps every assessment row, including one written a
  second ago: the field is `ge=0` and `prune_skipped_runs` compares
  `started_at < now - days * 86400`.

`observer.budget` (`BudgetConfig`):

- `max_cost_usd_per_day` (default `2.0`): ceiling on spend per day, per repository, over a rolling
  24 hours scoped to one checkout identity. Assessment rows spent nothing and do not count against
  it. Read by the assessment gate, which `auditr observer start` calls on every edit batch,
  suspect drain and verify pass.
- `max_runs_per_day` (default `40`): ceiling on runs per day, per repository. It is what bounds a
  model with no entry in the price table, and every fraction rule then reads remaining runs. Same
  reader.
- `max_budget_usd_per_run` (default `0.25`): ceiling handed to one run.
- `max_budget_usd_per_eval` (default `12.00`): ceiling on one `auditr graph eval` invocation,
  across every suite. The eval stops before opening a run that would cross it. A default
  `--suite all --sample 80` plans about 40 runs at `max_budget_usd_per_run` each, so the default
  covers that plan with headroom. The plan line and `--dry-run` show the worst case before any
  run opens.
- `low_budget_fraction` (default `0.25`, 0 to 1): remaining daily budget below which only
  high-value runs proceed. Strictly below: at exactly the fraction the bar has not been crossed.
  Under it, an edit batch counts only its `bare` and `self` new questions, and with no eval row for
  the runner about to be used, edit-triggered runs stop outright. `0` is the opt-out and means the
  rule never fires; a spent ceiling still stops every batch whatever this is set to. Read by the
  assessment gate.
- `max_utilization` (default `0.5`, 0 to 1): share of the rate-limit window the observer may take.
  Read by the SDK runner: a run whose reported utilization reaches it stops as `paused:ratelimit`,
  the same way one the window rejected outright does. `/api/status` carries it on each repo's rate
  limit meter.

`observer.limits` (`LimitsConfig`):

- `max_turns` (default `20`): agent turns before a run is cut off.
- `max_nodes_per_run` (default `12`): graph nodes one run may look at.
- `max_changes_per_run` (default `25`): proposals one run may commit.
- `max_open_runs` (default `8`): runs one process may hold staged at once, per repo identity. The
  oldest is evicted to make room, its `graph_runs` row finished `skipped` and its staging stored as
  rejections.
- `stranded_run_seconds` (default `3600`): seconds before a run still open is presumed dead.
  `auditr graph refinements prune` finishes those as `skipped` with a reason, and so does the
  observer's own session-start build, which is the first pass after a daemon that died mid-run
  comes back. Both open statuses are swept, on two windows: a `queued` row past this many seconds,
  a `running` row past twice as long, because a `running` row is open for a whole model call.
- `max_held_events` (default `500`): events a paused loop holds before the oldest are dropped. The
  spool is drained even while the loop cannot spend, so the batch is assessed when the pause lifts.
- `max_deferred_pairs` (default `200`): pairs an edit batch's node cap left behind that the loop
  carries to the next suspect drain. Newest kept.
- `max_paths_per_batch` (default `200`): edited paths one batch extracts facts for. A larger batch
  is truncated to its first paths and logs that it was.
- `max_queue_rows_per_pass` (default `500`): queue rows one suspect drain reads. The cap only ever
  takes the first `max_nodes_per_run` distinct nodes off the front, so the rest wait for the next
  pass.

`observer.scheduling` (`SchedulingConfig`). `debounce_seconds` (default `20`) is the quiet window
the loop collects an edit batch over; the window restarts on every event and the last one wins. It
restarts at most five times, so an edit stream faster than the window still yields a batch, and `0`
turns the window off: the batch is then whatever was already waiting when the loop looked.
`session_expiry_minutes` (default `45`) is how long after its last heartbeat a session still
counts, and `idle_shutdown_minutes` (default `30.0`, a float) is how long the daemon goes without a
request before exiting; `0` never exits, and the window is only consulted when no session is
attached. Both of those are the daemon's, and three more are its own clocks:

- `tick_seconds` (default `1.0`): how long the daemon blocks on its queue before looking at the
  clock again.
- `start_timeout_seconds` (default `10.0`): how long `auditr observer start` waits for the child
  to publish `daemon.json`.
- `stop_timeout_seconds` (default `10.0`): how long `auditr observer stop` waits for it to go.
  `auditr-observer` cannot read settings at all, so it carries both numbers as literals and a
  test pins them against these defaults.

The three the loop reads:

- `cooldown_minutes` (default `60`): minutes a pair a run already named is skipped by the suspect
  drain. `0` opts out, so every pair is drainable on every pass. Derived from `graph_runs`, because
  `graph_unresolved` is replaced wholesale by every build.

- `run_on_stale` (default `true`): re-run when an edit stales an existing refinement. Only a
  refinement anchored on a node the batch itself touched counts, so drift and no-op builds
  cannot trigger a run. The low budget bar does not narrow this arm, but the two rules that stop
  a batch outright, a spent day ceiling and a low budget with no eval row, are consulted first.
- `min_new_unresolved` (default `1`): distinct new unresolved callees an edit batch needs to earn
  a run. One name asked twice for two reasons is two queue rows and one question. The floor is
  `1`: a gate that fires on nothing opens a model-calling run for every batch that rebuilds.

Both of the two above are the edit batch's own bars. The suspect drain and the verify pass do not
read them: each has a cooldown of its own, and that is what brakes it.

The rest of the loop's clocks:

- `verify_cooldown_minutes` (default `60`): minutes between verify runs. A verify run that agrees
  activates the row and one that disagrees rejects it, but silence leaves it `pending`, which is
  the common case, so without this window one unsettled row would open a run on every tick. `0`
  opts out.
- `ratelimit_pause_minutes` (default `5.0`): how long a rate limit holds the loop when the runner
  named no reset instant. When it named one, that instant wins.
- `auth_pause_minutes` (default `15.0`): how long an auth refusal holds the loop before it re-asks
  the runner. The next run after the hold is the probe, and one that reaches the model clears it.
- `debounce_restart_cap` (default `5.0`): how many times the quiet window may restart before the
  batch is taken whatever is still arriving.
- `error_backoff_seconds` (default `5.0`) and `max_error_backoff_seconds` (default `300.0`): a pass
  that raises puts the repo in `paused:error` and its driver waits this long, doubling per
  consecutive failure up to the ceiling. A pass that finishes clears the count.

`observer.runner` (`RunnerConfig`):

- `agent` (default `"auto"`): `auto`, `claude` or `codex`. The default for `graph refine --runner`
  and `graph eval --runner`; `codex` is refused today.
- `model` (default `"haiku"`): `haiku` or `sonnet`, the Claude tier a refinement run uses and the
  default for `--model`.
- `codex_model` (default `""`) and `codex_prices` (default `{}`): the Codex model override and its
  price table. `codex_model` is the model the daemon's `/api/evals` looks the Codex runner's latest
  eval row up under, falling back to `model` when it is empty. `codex_prices` has no reader today,
  since the Codex runner is refused.

`observer.tuning` (`TuningConfig`). `mode` (default `"propose"`) and `stopwords_max` (default `20`)
govern knob-tuning proposals and have no reader today. The one the tier gate reads:

- `min_precision` (default `0.95`, 0 to just under 1): the Wilson 95 per cent lower bound a
  stratum's measured precision has to reach before that shape may go active. Read off the latest
  `graph eval` row per suite and stratum, so a later failing eval takes activation back. At 0.95 a
  flawless run needs 73 trials to clear it. 1.0 is refused: `wilson_lower(n, n)` is below 1.0 for
  every finite `n`, so no run of any size could ever meet it. See [graph.md](graph.md).

### `vectors` (`VectorsConfig`)

- `enabled` (default `false`): enable the opt-in `sqlite-vec` plus static-embedding layer.
- `model` (default `"minishlab/potion-base-8M@bf8b056"`): the pinned model and revision.

### User environment variables

| Form | Example | Notes |
| --- | --- | --- |
| One nested field | `AUDITOR_USER_OBSERVER__RUNNER__MODEL=sonnet` | `__` separates levels. |
| A whole table | `AUDITOR_USER_OBSERVER='{"runner":{"model":"sonnet"}}'` | JSON value, merged over both files. |
| A top-level field | `AUDITOR_USER_CONFIG_VERSION=1` | Field name uppercased. |

- Both forms deep-merge over the two JSON files, so setting one field leaves its siblings alone.

## Environment variables

### Global paths (`GlobalPaths`)

| Var | Default | Purpose |
| --- | --- | --- |
| `AUDITOR_HOME` | `~/.auditor` | Root of all generated global state: `index.db` (the shared index, partitioned by repo, holding cached findings, persistent ignores and graph facts), `bin/` (the checksum-verified osv-scanner download), `osv-db/` (the OSV database). |
| `AUDITOR_CODE_MODE` | unset (`false`) | Enables the experimental Code Mode transform on the MCP server. A no-op unless the `code-mode` extra is installed ([auditr-mcp.md](auditr-mcp.md)). |
| `AUDITOR_REFINE_RUN` | unset | Pre-binds the MCP refinement tools to one run id, so a runner-spawned server needs no `run_id` per call ([auditr-mcp.md](auditr-mcp.md)). |
| `AUDITOR_OBSERVER` | unset (on) | Set to `0`, `f`, `false`, `n`, `no` or `off` to disable the observer entirely: every verb prints a notice and exits 0. Read by `paths.observer_enabled`, which ignores a value it cannot read rather than failing the command. |
| `AUDITOR_OBSERVER_PORT` | unset (hashed) | The loopback port the daemon binds. Unset, it is `7490 + crc32(resolved $AUDITOR_HOME) % 500`; `0` asks the kernel for any free port. There is no `observer.port` config key: this is env only ([observer.md](observer.md)). |

### Repo settings (`AuditorSettings`)

The environment reaches one field, not the whole model.

| Var | Default | Purpose |
| --- | --- | --- |
| `AUDITOR_RESPECT_GITIGNORE` | `true` | Set to `false` to scan git-ignored files. Same as `respect_gitignore` in TOML. |

- Every other field is repo policy and is ignored when it appears in the environment: `AUDITOR_RULES`,
  `AUDITOR_EXCLUDE`, `AUDITOR_TEST_MODE` and the rest read as if unset, with no error. Policy is
  shared through git and drives CI, so a shell variable must not disable a rule the repo leaves
  unmentioned. Put them in TOML, or pass `--config-json` for one run.
- The list is an allow-list in `_NonPolicyEnvSource`, so a field added to `AuditorSettings` is
  policy until someone puts it there on purpose.
- The environment is the lowest layer. It is deep-merged under the TOML layers, so an `AUDITOR_*`
  value only reaches keys no profile or repo file sets.
- `AUDITOR_EXTENDS` never applies, and two independent locks keep it that way: `extends` is not on
  `_NonPolicyEnvSource`'s allow-list, and the loader writes the resolved profile into the merged
  config as its last step. Use `extends` in TOML or `scan --profile`. `tests/test_config.py`'s
  `test_env_cannot_choose_a_profile` pins both.
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
