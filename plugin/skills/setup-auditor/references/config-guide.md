# plugin/skills/setup-auditor/references/config-guide.md
# Configuring auditor

Source of truth: `auditor/config.py` (`AuditorSettings` + friends), `auditor/profiles/*.toml`.
Confirm against the installed version before relying on any of this: `auditr config show`,
`auditr --help`.

## Where config lives

Two equivalent places, standalone wins on conflict:

- `[tool.auditor]` table in `pyproject.toml`
- a standalone `.auditor/config.toml`

Both are optional — with neither present, `extends` defaults to `"base"` (verified: a repo with
no `.auditor/` and no `[tool.auditor]` table still gets a full `auditr config show`, `"extends":
"base"`). A repo counts as "configured" (`is_configured()`) the moment either file exists, even
if it's just `extends = "strict"` with nothing else.

Any setting can also be overridden ad hoc, no file edit, via `--config-json '<json>'` on the CLI
or the `config` kwarg on the MCP `scan` tool — deep-merged as the *highest* layer (wins over both
files above). Handy for a one-off experiment or a CI-only override.

## The shape (`auditr config show`)

Top-level keys on the resolved `AuditorSettings`, grouped by what they control:

| Key | Default | What it does |
|---|---|---|
| `extends` | `"base"` | Profile chain root — `base \| strict \| pydantic \| all-strict` or a path to a custom profile TOML. See **Choosing a profile** below. |
| `exclude` | `[]` | Extra globs to skip, on top of the built-in defaults (vendor dirs, generated files, `.venv`, …). |
| `respect_gitignore` | `true` | Skip git-ignored files. `scan --include-gitignored` overrides per-run. |
| `resolve_packages` | `[]` | Dotted-name prefixes of first-party dependency packages whose *installed* source the cross-file resolver may follow into (from the scanned project's `.venv`). Repo-local resolution always works; this extends it into a shared lib. |
| `diff_base` | `null` (auto-detect) | What `scan --vs-base` diffs against. Auto-detects `main`/`master`/`develop`/`development`; pin it explicitly (`"origin/main"`) if the repo's default branch is unusual. |
| `threshold.*` | see below | Tunable floors, grouped by concern: `oop`, `size`, `dry`, `jsx`, `test`. Every floor is a self-documenting `Field` (`ge=1`) — e.g. `threshold.size.max_complexity` (default 10), `threshold.oop.wall_kwarg_min` (default 12), `threshold.dry.dup_block_min_statements` (default 3). |
| `rules` | `{}` | Per-rule override: `{ enabled, severity, verdict_kind, threshold }`, all optional (unset = inherit). Validated against the registry — an unknown `rule_id` fails config load with a "run `auditr rules list`" hint. |
| `categories` | `{}` | Per-category override: `{ enabled, min_severity }`. `min_severity` is a *floor* — it raises a rule's severity if it would otherwise be lower, never lowers it. |
| `roles` / `role_globs` | role-dependent | How each `FileRole` (`production \| test \| test_support \| script \| generated`) is audited — `mode: relaxed \| strict \| excluded`, plus role-scoped `rules`/`categories`. Defaults: tests relaxed, generated excluded, everything else strict. |
| `test_mode` | `null` | Force `relaxed`/`strict`/`excluded` for test-role files regardless of the profile. `scan --strict-tests`/`-t` is the per-run version. |
| `overrides` | `[]` | Per-glob (or per-role) overrides applied last, ruff `per-file-ignores` style: `{ path, role, rules, categories }`. |
| `plugins` | `[]` | Config-named plugin modules to import (`["acme.rules"]`) — unconditional, no trust gate (see `write-detector` skill). |
| `trust_local_plugins` | `false` | Load `.auditor/plugins/*.py` unconditionally. The `-a`/`--allow-local-plugins` CLI flag is the per-run equivalent. |
| `settings_modules` / `settings_cohesion` | `["config", "settings"]` / `true` | Where `PY-CONFIG-SCATTERED-SETTINGS` expects `BaseSettings` subclasses to live. |
| `cli_frameworks` | `["typer", "click"]` | CLI frameworks whose free-function-command idiom is exempted from the OOP-orchestrator / cross-file duplicate-function heuristics. |
| `design_system` | empty | `[tool.auditor.design_system]` — a project's declared DS primitives. The `design-system` TS rules stay silent until a repo opts in (see the TS/React section of the top-level README). |
| `sqlalchemy` | both `false` | `expire_on_commit` / `async_session` — facts about the project's session factory the auditor can't see from a model file alone. Setting either activates a dormant rule (`SA-GREENLET-ATTR-AFTER-COMMIT` / `SA-IMPLICIT-LAZY-ASYNC`). |
| `graph` | `enabled: false` | `[tool.auditor.graph]` — the semantic graph pass (needs the `graph` extra). `enabled = true` makes a plain `scan -i` also populate graph facts; otherwise use `auditr graph build` explicitly. |
| `malware_scan` | `enabled: false` | `[tool.auditor.malware_scan]` — the opt-in ClamAV + osv-scanner shell-outs. `enabled = true`, or per-run `scan --malware`. |

Verified example — a minimal standalone config that just picks a profile:

```toml
# .auditor/config.toml
extends = "strict"
```

`auditr config show` after writing that (in an otherwise-unconfigured repo) confirms
`"extends": "strict"` and `"categories": {"oop-composition": {"enabled": true, ...}}` — the one
thing `strict` adds over `base`.

## Choosing a profile

Profiles live in `auditor/profiles/*.toml` and chain via `extends` (a profile can extend
another). What each one actually contains, read straight from the files:

- **`base`** — the industry-standard floor. Security, malware, secrets, supply-chain,
  correctness, async, typing, config, and cross-file dedup are **on**. The opinionated
  `oop-composition` category is **off** (`[categories] "oop-composition" = { enabled = false }`),
  except five specific rules it force-enables anyway (`PY-OOP-MODEL-REBUILD`,
  `PY-OOP-DICT-MUTATION-BUILDER`, `PY-OOP-MODULE-CONST-FOR-SUBCLASS`, `PY-OOP-CLOSURE-CAPTURE`,
  `PY-OOP-FLAT-FIELD-MODEL`). Test/test_support roles run `relaxed` (assert-for-auth,
  hardcoded-secret, and friends are disabled; a handful of security rules downgrade to
  `candidate` instead of fully off); `script` is `relaxed`; `generated` is `excluded`. **Pick
  this** for an unfamiliar or legacy codebase, a first onboarding pass, or any repo where you
  want signal on real bugs/security without opinionated style pushback yet.
- **`strict`** (`extends = "base"`) — adds the whole `oop-composition` category: constructor
  walls, god classes, flat-field models, dispatch ladders, duplicate blocks, parallel siblings,
  field-by-field copying, complexity ceilings. Same relaxed test/script/generated role policy as
  `base`. **Pick this** once a repo is past initial triage and you want composition/DRY house
  rules enforced too — this repo's own `pyproject.toml` runs `strict`.
- **`pydantic`** (`extends = "strict"`) — currently identical to `strict` (no additional
  overrides in `pydantic.toml` as of this writing; it's a distinct extension point reserved for
  Pydantic-specific tuning, not yet populated). The Pydantic-aware rules
  (`PY-PYDANTIC-V1-CONFIG-CLASS`, `PY-OOP-DATACLASS-IN-PYDANTIC`) are gated on `ctx.project_deps`
  (`"pydantic" in ctx.project_deps`), not on this profile — they already fire under `base`/
  `strict` in any repo that depends on pydantic. **Pick this** if you want the profile name to
  self-document "this is a Pydantic-first codebase" for readers of the config, functionally
  equivalent to `strict` today.
- **`all-strict`** (`extends = "strict"`) — flips every role to `mode = "strict"`
  (`roles.test`, `roles.test_support`, `roles.script`), so tests and scripts are audited at full
  production strength instead of the relaxed default. **Pick this** for a from-scratch repo
  where you want zero relaxed carve-outs from day one, or when auditing test code quality itself
  matters as much as production code.

Override the profile for a single run without touching config: `scan -p strict` (or any other
profile name/path). Useful to preview what a stricter profile would surface before committing to
it in `.auditor/config.toml`.

## Baseline strategy

Purpose: adopt auditor on a repo that already has findings, without drowning day one in
pre-existing issues — gate only on what's *added* from here.

```bash
auditr scan . --write-baseline .auditor/baseline.json   # snapshot current findings, then exit
auditr scan . --baseline .auditor/baseline.json          # report only NEW findings
auditr scan . --baseline .auditor/baseline.json --fail-on high   # CI gate: only new high+ trips it
```

Verified end-to-end on a fresh two-finding demo repo: `--write-baseline` reports `Wrote baseline
.auditor/baseline.json — N finding(s) recorded`; re-scanning with `--baseline` against the
unchanged repo reports `✓ clean` plus `N pre-existing finding(s) hidden by baseline`; adding one
new violation and re-scanning with the same `--baseline` reports *only* that one new finding
(the pre-existing ones stay hidden); `--fail-on high` against that same baseline exits non-zero
only because of the new finding.

Each finding is fingerprinted by `(file, rule, hash(offending text))`, not by line number — a
finding survives unrelated edits elsewhere in the file, and genuinely new code still surfaces.
Fingerprints are counted, not deduplicated: three pre-existing untyped `def __init__(`s are all
baselined, and a fourth one added later still reports.

**When to write one**: any repo with existing history where a first `scan` returns more than a
handful of findings and the team wants adoption without a triage sprint first. **When to skip
it**: a brand-new repo (baseline would just be `--write-baseline` against zero findings — pointless)
or a repo small enough to actually fix the initial findings outright instead of grandfathering
them.

## Install extras

`auditr` core has no extras required for Python auditing. Add extras based on what the repo
actually needs — don't install extras a repo won't use:

| Extra | Adds | Add it when |
|---|---|---|
| `mcp` | `fastmcp`, puts `auditr-mcp` on PATH | Always, if the repo will be audited from an agent/MCP client rather than pure CLI/CI. The Claude Code plugin bundles its own `uvx --from auditr[mcp] auditr-mcp` — installing this extra system-wide is for using the MCP server outside the plugin (Codex, a bare `claude mcp add`, etc). |
| `ts` | `tree-sitter`, `tree-sitter-typescript` | The repo has `.ts`/`.tsx` files auditor should parse — without it, TypeScript/React detectors don't run. |
| `graph` | `numpy`, `scikit-learn`, `snowballstemmer`, `networkx` | The repo is large/complex enough that "god concept" / "scattered concept" / naming-consistency queries (`auditr graph ...`) add value, or the team wants `explore-graph`-skill-level usage-tracing (find-refs, blast radius) beyond what per-file findings give. |

```bash
uv tool install "auditr[mcp,ts,graph]"    # pick the subset that applies
uv tool install "auditr[mcp]"             # Python-only repo, agent-driven — the common case
uv tool install auditr                    # CLI/CI-only, no MCP, pure Python repo
```

`pip install`/`pipx install` accept the same extras syntax (`pip install "auditr[mcp,ts]"`).

## MCP registration

- **Using the Claude Code plugin** (`claude plugin marketplace add Sung96kim/auditor` +
  `/plugin install auditor`, or `claude --plugin-dir ./plugin` for local dev): the plugin ships
  `plugin/.mcp.json` (`uvx --from auditr[mcp] auditr-mcp`) and Claude registers it automatically
  — **no separate `claude mcp add` needed**. This is the default path for setting up auditor via
  this skill.
- **Outside the plugin** (bare `auditr-mcp` on PATH, another agent harness, or a non-Claude-Code
  setup): register explicitly.

  ```bash
  claude mcp add auditor -- auditor-mcp                      # local scope, this project only
  claude mcp add --scope user auditor -- auditor-mcp         # every project on this machine
  claude mcp add --scope project auditor -- auditor-mcp      # writes .mcp.json — commit it, teammates get it too
  ```

  If `auditor-mcp` isn't on PATH (installed into a project venv, not as a `uv tool`):

  ```bash
  claude mcp add auditor -- uv run --directory /path/to/auditor auditor-mcp
  ```

  Verify with `claude mcp list`.
- **Codex CLI**: `codex mcp add auditor -- auditor-mcp`, or a `[mcp_servers.auditor]` block in
  `~/.codex/config.toml` / `.codex/config.toml` (`command = "auditor-mcp"`, `args = []`).

## Worked onboarding walkthrough

A fresh repo, no `.auditor/`, no `[tool.auditor]` — verified end-to-end:

```bash
$ auditr version
auditr 0.8.0

$ auditr discover .
[{"file": "app.py", "role": "production"}]

$ auditr scan .                       # defaults to extends="base" with no config at all
5 findings in 1 of 1 files
  high 1   medium 2   low 2
```

Scaffold config, opt into `strict` (this repo has room to grow into composition rules):

```bash
$ mkdir -p .auditor && printf 'extends = "strict"\n' > .auditor/config.toml
$ auditr config show   # confirm: "extends": "strict", oop-composition category now enabled
```

Baseline today's findings so the team adopts without a triage sprint:

```bash
$ auditr scan . --write-baseline .auditor/baseline.json
Wrote baseline .auditor/baseline.json — 5 finding(s) recorded

$ auditr scan . --baseline .auditor/baseline.json
✓ clean — 1 files, no findings
5 pre-existing finding(s) hidden by baseline
```

From here, `.auditor/baseline.json` gets committed and CI runs
`scan --since <base> --baseline .auditor/baseline.json --fail-on high` — new code is held to the
gate, the legacy 5 are grandfathered until someone gets to them. MCP registration is automatic if
this is being driven from the Claude Code plugin; otherwise follow **MCP registration** above.
