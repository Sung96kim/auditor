# scan reference

`scan` audits a file or directory and prints a concise human summary by default; machine
formats are opt-in. `auditr scan --help` lists every flag. Nothing extra is needed for a plain
scan; the diff-scoping flags need a git repository, and `--malware` needs the opt-in backends
installed.

## Common invocations

```bash
# repo scan, readable summary on stdout
auditr scan .

# machine output for an agent or CI
auditr scan . -f json

# HTML report written to a file instead of stdout
auditr scan . -f html -o audit.html

# use the shared cache so a re-scan only re-audits what changed
auditr scan . -i

# one file on its own: no index, no cross-file pass
auditr scan path/to/file.py --isolated

# run any repo at strict strength without editing its config
auditr scan . -p strict

# report only the files you changed, and fail CI on high or worse
auditr scan --vs-base --fail-on high

# log progress to stderr: -v files, -vv detail, -vvv per finding
auditr scan . -vvv
```

- `-f` or `-o` replaces the human summary with the rendered report; with neither, `scan` prints
  the summary and nothing parseable.
- `-n` beats `-i`: `--no-index` forces a stateless run even when `--incremental` or a diff flag
  asks for the cache.
- `--isolated` only changes a single-file target. On a directory it has no effect; the
  cross-file pass still runs.
- The diff flags resolve in one order: `--vs-base`, then `--since`, then `--changed`.
- Any diff flag turns `--incremental` on unless `--no-index` is passed.
- `--write-baseline` writes the snapshot and returns; the gate, display filters, format flags,
  and the status file are all skipped.
- `--malware` exits with an error when neither ClamAV nor osv-scanner is on PATH. See
  [malware.md](malware.md).
- `-a` loads `.auditor/plugins/*.py` for the run. See [plugins.md](plugins.md).
- `--config-json` merges a JSON object over the resolved config as the highest layer. See
  [configuration.md](configuration.md).

## What gets scanned

- `TARGET` defaults to `.`. A directory is walked; a file is audited on its own.
- Files come from `git ls-files` inside a repo, so `.gitignore` is honored exactly; outside a
  repo it is a tree walk.
- A file is auditable when its extension belongs to a registered language, or when its filename
  matches a manifest the auditor knows. `auditr plugins list` prints the registered languages.
- Git-ignored files are skipped unless `--include-gitignored`.
- Vendor and build directories, the default generated-file globs, and soft-skipped migration
  directories are all dropped before anything is read. The exact sets are in
  [discover.md](discover.md).
- Every file is classified into a role. Role decides how strictly it is audited; `-t` audits
  test-role files at production strength. See [discover.md](discover.md).
- Files no language auditor claims still go through a content secret sweep. Binaries and files
  over 2 MB are skipped in that sweep.
- Without the `ts` extra installed, no language auditor claims `.ts` and `.tsx` files. Nothing
  warns and nothing fails: they fall through to that secret sweep and no TypeScript rule runs.
- A dotenv file tracked by the repo is `CFG-ENV-FILE-COMMITTED`, a blocking finding. Names ending
  `.example`, `.sample`, `.template`, `.dist` or `.defaults` are exempt, since they are meant to be
  committed.
- `--root` pins the project root; by default it is the nearest ancestor holding `.git`,
  `pyproject.toml`, or `.auditor`.

## Scoping the output

- `--since <ref>`, `--changed`, and `--vs-base` scope which files are reported, not which are
  scanned. The whole repo is still scanned so cross-file rules stay correct.
- Each reported file is audited in full, never just the diff hunks.
- `--since` takes any ref git resolves locally: a branch, `origin/<branch>`, `HEAD~3`, a tag, a
  SHA. Only local `git diff` and `git ls-files` run, so ssh and https remotes behave identically.
- An unresolvable ref exits with an error telling you to fetch it first.
- Untracked files count as changed.
- `--vs-base` uses the configured `diff_base`, falling back to the first of `main`, `master`,
  `develop`, `development` that exists locally or under `origin/`. It exits with an error when
  none resolve.
- `-s`, `-m`, and `--rule` filter what is displayed, after the gate has already been decided, so
  they never change the exit code.
- `--rule` validates ids against the registry and suggests a near match on a typo. See
  [rules.md](rules.md).

## Output formats

- Default (no `-f`, no `-o`): a summary on stdout with severity counts, the worst files, and
  notes for cached, suppressed, and ignored counts.
- `-f json` renders the full shape: per-file counts, findings with evidence, skipped rules, and
  totals. The same shape `report` emits, documented in [report.md](report.md).
- `-f sarif` renders SARIF 2.1.0 for code-scanning dashboards; `-f md` and `-f html` render
  human reports.
- A bad `-f` fails before the scan starts and lists the accepted formats. `auditr plugins list`
  prints every registered reporter, including plugin-added ones.
- `-o PATH` writes the rendered report to that path and confirms on stderr.
- `--serve` renders HTML, binds an ephemeral port on `127.0.0.1`, opens a browser, and holds
  until Ctrl-C. It returns before the gate's exit code is applied, so it never exits non-zero.

## The gate

- `--fail-on <severity>` exits 1 when any finding is at or above that severity.
- Only `auto` findings count. A `candidate` finding is evidence for an agent to judge and never
  breaks CI on its own.
- The gate runs after baseline filtering and after persistent ignores, and before the display
  filters, so it fires only on new, unignored findings and is unaffected by `-s`/`-m`/`--rule`.
- Exit codes: 0 when the gate is not tripped, 1 when it is or when a flag, config, or ref is
  invalid.

## Incremental index

- `-i` uses and updates the shared SQLite index, partitioned per repo. See [index.md](index.md).
- Caching is per rule per file: a rule re-runs when the file's content hash changes or when that
  rule's effective configuration changes.
- Files that no longer exist are pruned from the index within the scanned scope, so a deleted
  file leaves no stale findings behind.
- `-n` forces a stateless run and reads no cache.
- A single-file `scan` still opens the shared index unless `--isolated` or `-n`, so it can report
  cross-file findings. On a cold index it warms the whole repo once, then returns only that file.
- With no index at all, a directory scan still computes the cross-file pass in memory; it is just
  not persisted.

## Baseline

- `--write-baseline PATH` records the current findings and exits. Use it to accept an existing
  repo's findings before turning the gate on.
- `--baseline PATH` hides recorded findings and reports only new ones. A missing file is an error
  naming the `--write-baseline` command to create it.
- A finding is fingerprinted as `(file, rule id, hash of the offending text)`, independent of line
  number, so it survives edits elsewhere in the file while genuinely new code still surfaces.
- Fingerprints are counted, not deduplicated: three identical untyped `def __init__(` are three
  records, and a fourth one added later still shows up.
- Filtering runs before the gate, so `--baseline` plus `--fail-on` fires only on new findings.
- The hidden count is printed to stderr in summary mode only; machine formats keep stdout clean.

## Skips and ignores

- `--no-skips` ignores every in-source `# auditor: skip` and `# auditor: skip-file` directive, for
  an un-silenceable sweep.
- Suppressed findings are counted and reported, never dropped silently.
- Persistent ignores stored in the shared index are applied automatically. `--show-ignored` puts
  them back into the output, which also puts them back into the gate's input.
- The directive syntax, the comment markers it accepts, and where a directive has to sit for the
  engine to see it are all in [ignore.md](ignore.md).
- `-x/--exclude` adds globs on top of the configured `exclude`, repeatable. See
  [configuration.md](configuration.md).

## Status file

- A directory scan writes `$AUDITOR_HOME/repos/<repo_dir_key>/status.json`: per-severity counts,
  whether the repo has auditor configuration, and a write timestamp, all under a `scan` key.
- Only a full scan of the repo root writes it. `--since` / `--changed` / `--vs-base` and a
  subdirectory target report part of the tree, so they leave the last full scan's counts in place
  rather than filing a partial roll-up as the repo's posture.
- The write happens before baseline filtering, so `--baseline` records what is in the tree, not
  what the gate chose to show.
- Nothing is written into the repository. `repo_dir_key` and the rest of the layout are in
  [configuration.md](configuration.md).
- The file holds one block per writer and each writer merges only its own, so a second writer's
  block survives.
- A single-file `scan` and `report` do not write it.
- The write is best effort; a read-only or missing home does not fail the scan.
- It is the only thing the Claude Code plugin's status line reads. See
  [claude-code-plugin.md](claude-code-plugin.md).
