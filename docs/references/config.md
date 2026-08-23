# config reference

`auditr config show` prints the configuration the auditor resolved for a repo, after layering the
profile chain, the repo's TOML, and any injected override. `auditr config show --help` lists every
flag. What each field means and which environment variables exist is in
[configuration.md](configuration.md); this page covers how a value gets its final form.

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

## Profiles

- `extends` names the profile chain; the built-in profiles and what each enables are in
  [configuration.md](configuration.md). `config show` takes no profile flag, unlike `scan` and
  `report`, which take `-p`/`--profile`.

## Overrides and failures

- `--config-json` takes a JSON object in the same shape as the TOML config and is merged as the
  highest layer. `scan`, `report`, and `discover` accept the same flag.
- Invalid JSON, or JSON that is not an object, fails with a one-line error and no traceback.
- A value that fails validation fails the same way, naming the offending field and the reason.
- Rule ids and categories in the config are validated against the runtime registry, so plugin
  rules are admissible; see [plugins.md](plugins.md).
