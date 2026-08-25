# config reference

`auditr config show` prints the configuration the auditor resolved for a repo, after layering the
profile chain, the repo's TOML, and any injected override. `auditr config check` reports keys no
model declares. `auditr config --help` lists every flag. What each field means and which
environment variables exist is in [configuration.md](configuration.md); this page covers how a
value gets its final form.

## Common invocations

```bash
# the resolved configuration for the repo containing the working directory
auditr config show

# resolve another checkout
auditr config show -r ../other-repo

# preview an override before committing it to a file
auditr config show --config-json '{"threshold":{"size":{"max_complexity":8}}}'

# raw JSON
auditr config show --json

# the personal settings resolved for this repo ($AUDITOR_HOME plus AUDITOR_USER_*)
auditr config show --user

# list config keys no model declares, in both the repo policy and the user settings
auditr config check
```

## How a value is resolved

- Layers, later wins: the built-in profile chain named by `extends`, then `[tool.auditor]` in
  `pyproject.toml`, then `.auditor/config.toml`, then `--config-json`.
- Layers are deep-merged, so a repo overrides one nested threshold without restating the rest.
- The printed object is the validated settings model, so every field shows its effective value,
  including defaults the repo never wrote down.
- `extends` in the output is the profile the run actually used.
- The repo root is the nearest directory at or above `-r`/`--root` (default `.`) that holds
  `.git`, `pyproject.toml`, or `.auditor`.

## Checking a config

- `config check` prints one row per unknown key with its dotted path, for the repo policy and for
  the user settings, under the root it resolved. Unknown keys are ignored at load time, so this
  is how a typo surfaces.
- It exits 0 with warnings and 1 when a value fails validation, naming the field and the reason.
- Field *names* are reported as unknown keys and never fail the command. *Values*, and the keys of
  a keyed table (a role name, a category name), are validated and fail it: `[roles.tets]` exits 1
  naming `roles.tets`, it is not listed as an unknown key.
- `config show --user` prints the resolved `UserSettings` instead of the repo policy: model
  defaults, `$AUDITOR_HOME/config.json`, `$AUDITOR_HOME/repos/<key>/config.json`, then
  `AUDITOR_USER_*`. It warns about unknown user keys the same way the repo branch does.
- `--config-json` is repo policy, so combining it with `--user` exits non-zero rather than being
  silently dropped.
- The files themselves are created by [`auditr init`](init.md).

## Profiles

- `extends` names the profile chain; the built-in profiles and what each enables are in
  [configuration.md](configuration.md). `config show` takes no profile flag, unlike `scan` and
  `report`, which take `-p`/`--profile`.

## Overrides and failures

- `--config-json` takes a JSON object in the same shape as the TOML config and is merged as the
  highest layer. `scan`, `report`, and `discover` accept the same flag.
- Invalid JSON, or JSON that is not an object, fails with a one-line error and no traceback.
- A profile name that is neither a built-in nor a readable `.toml` path fails the same way, naming
  the built-ins. It applies to `--profile`, to `extends` in the repo's TOML, and to an `extends`
  passed through `--config-json`.
- So do the other two ways a config can be found and still be unusable: an `extends` chain that
  leads back to a profile already being loaded, and a config or profile file that does not parse
  as TOML, which names the file. Every command surface catches the three of them together.
- A value that fails validation fails the same way, naming the offending field and the reason.
- Rule ids and categories in the config are validated against the runtime registry, so plugin
  rules are admissible; see [plugins.md](plugins.md).
