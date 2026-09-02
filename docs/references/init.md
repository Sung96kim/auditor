# init reference

`auditr init` creates the user config home under `$AUDITOR_HOME` (default `~/.auditor`): the
global settings file, the JSON Schema editors complete against, and, with `--repo`, the per-repo
overlay and its breadcrumb. Nothing is written into the repository. `auditr init --help` lists
every flag.

## Common invocations

```bash
# create the home and the global settings file
auditr init

# also create this repo's personal overlay and breadcrumb
auditr init --repo

# work against another checkout instead of walking up from here
auditr init --repo --root ../other-repo

# report only: unknown keys, a moved checkout, a leftover status file
auditr init --check

# point a moved checkout's breadcrumb at its new root
auditr init --repo --migrate

# delete a leftover .auditor/.status.json from an older auditr
auditr init --clean-status

# machine-readable
auditr init --json
```

- `--root/-r` picks the repo whose config and plugins load, defaulting to a walk up from the
  current directory. It selects the repo `--repo`, `--migrate` and `--clean-status` act on; it
  never changes where the home itself lives, which is `$AUDITOR_HOME` alone. `config show`,
  `config check`, `rules list`, `plugins list`, `index add`, `index list`, `index forget` and
  every `ignore` subcommand take the same flag ([config.md](config.md), [index.md](index.md),
  [rules.md](rules.md), [plugins.md](plugins.md), [ignore.md](ignore.md)). `index repos` reads
  the whole registry and takes none.

## What it writes

- `$AUDITOR_HOME/config.json`: `"$schema": "./config.schema.json"` and `"config_version": 2`, plus
  whatever keys were already there. Defaults are never written out, so a later default change is
  not pinned and a value the user chose stays distinguishable from one init wrote.
- `$AUDITOR_HOME/config.schema.json`: generated from `UserSettings`, descriptions included. The
  `observer` table is grouped into `budget`, `limits`, `scheduling`, `runner` and `tuning`, so an
  editor completes one group at a time. Re-run `auditr init` after upgrading to refresh it.
- `--repo` adds `$AUDITOR_HOME/repos/<repo_dir_key>/config.json` with
  `"$schema": "../../config.schema.json"`, and `root.json`, the breadcrumb
  `{root, identity, created_at}`.
- Re-running rewrites only `$schema` and `config_version`. Every other key is left alone.
- A settings file that is not a JSON object stops the command with exit 1 and is left
  untouched, in both write and `--check` mode. Fix or delete it, then re-run.
- Writes go through a temp file and a rename, so an interrupted run never truncates a
  settings file.
- `config_version` is written from `UserSettings.config_version`'s default, so the marker and the
  model cannot disagree. Migration starts at the first bump; there is nothing to migrate at 1.
- The full layout and every settings key is in [configuration.md](configuration.md).

## Checks

- `--check` writes nothing. It lists unknown keys with their dotted path, reports a moved
  checkout, and reports a leftover `.auditor/.status.json`. Its report says `not written
  (--check)`, never `up to date`.
- Both modes list unknown keys from both families, repo policy first and then the user settings,
  in `unknown_keys`. `init` opts out of the stderr notice every other command prints, so the two
  lists have to be the same one.
- `--migrate` and `--clean-status` both write, so combining either with `--check` exits non-zero
  rather than doing nothing.
- A moved checkout is one whose breadcrumb names a root that no longer exists. Two live worktrees
  share one identity by design, so a sibling root that still exists is not a move.
- `--migrate` rewrites that breadcrumb to the current root. It requires `--repo`, since the
  breadcrumb only exists alongside the per-repo file; on its own it exits non-zero. Once it has
  run, the report says the breadcrumb now points here instead of asking for `--migrate` again.
- `root.json` records one root, and every worktree of a checkout shares the directory, so the
  breadcrumb names whichever worktree last ran `auditr init --repo --migrate`. It is a hint for
  the moved-checkout check, never an identity.
- `--clean-status` deletes `<repo>/.auditor/.status.json`, which older releases wrote and nothing
  reads any more. Without the flag its presence is only reported.
- A settings file that predates `config_version` 2 stops the write: the marker would claim a shape
  the file does not have. The message names every moved key and its new path. `--force` stamps the
  version anyway and leaves every key in place, as `init` always does.
- `auditr config check` runs the unknown-key half against both the repo policy and the user
  settings. See [config.md](config.md).
- An unwritable or file-occupied `$AUDITOR_HOME` exits 1 with a one-line message, no traceback.
