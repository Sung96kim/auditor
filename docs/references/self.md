# self reference

`auditr self update` checks PyPI for a newer `auditr` release and optionally installs it,
reproducing how the current copy was installed. `auditr self update --help` lists every flag.
It needs network access to `pypi.org`; the upgrade itself needs `uv` or `pip`, depending on the
install.

## Common invocations

```bash
# check, then prompt before upgrading
auditr self update

# only report whether a newer release exists
auditr self update --check

# upgrade without the confirmation prompt
auditr self update -y

# compare against pre-releases too
auditr self update --pre
```

- `auditr self` on its own prints the subcommand list.
- `--check` and "already current" both stop after the version panel; nothing is installed.

## What it does

- Reads the installed `auditr` version, fetches `https://pypi.org/pypi/auditr/json`, and picks
  the highest release. Pre-releases are excluded unless `--pre`.
- Prints a panel to stderr with the installed version, the latest version, and whether an update
  is available.
- Confirms interactively unless `-y`, then runs the upgrade pinned to the exact version it
  reported, and tells you to restart any running auditr processes.
- A PyPI failure (network, timeout, unexpected payload) exits 1 with the reason.

## How the install is detected

- A `uv tool` install is recognized by a `uv-receipt.toml` next to the running interpreter under a
  path containing `tools`. It is upgraded with `uv tool install auditr==<version> --force`,
  reusing the Python the receipt recorded. That venv has no pip, so this path is required.
- Any other install (pip, pipx, a plain venv) upgrades with
  `python -m pip install --upgrade auditr==<version>`, falling back to
  `uv pip install --upgrade` when pip is not importable.
- With neither pip nor uv available it exits 1 and prints the command to run by hand.
- The upgrade always targets the environment auditr is running in. An ephemeral environment, such
  as one created by `uvx auditr`, has no uv-tool receipt, so install with `uv tool install auditr`
  when you want an install that `self update` can upgrade in place.

## Extras

- Extras are carried through the upgrade, so an install with extras stays that way.
- For a `uv tool` install they come from the requirement recorded in the receipt.
- For a pip or venv install they are inferred from what is present: an extra counts only when
  every one of its dependencies is importable, so the inference never over-claims.
- An extra the target release no longer offers is dropped with a note on stderr rather than
  failing the upgrade.
- Release metadata that does not declare its extras is treated as unknown, and every detected
  extra is kept.

## When the upgrade fails

- The installer's stdout is hidden and its stderr is captured while the progress animation runs.
- On a non-zero exit, `self update` prints the exact command it ran plus the captured stderr, and
  exits 1.

## version

- `auditr version` reads the installed `auditr` distribution's version, falling back to
  `auditor.__version__` in a source checkout.
- Piped, it prints `auditr <version>` on one line and makes no network call.
- At a TTY it prints a panel with that version, the running Python, the install path, and its own
  short-timeout PyPI check, which reports up to date, the newer version available, or offline.
