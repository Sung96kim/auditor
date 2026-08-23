# index reference

`auditr index` manages the audit-scope index and the incremental cache that `scan -i`,
`aggregate`, and `crossfile` read. `auditr index --help` lists every flag. Nothing extra to
install: the database is created the first time any command touches it.

## Common invocations

```bash
# register files as the audit scope
auditr index add src/app.py src/db.py

# the indexed files for this repo, with their per-severity finding counts
auditr index list

# every repo registered in the shared index
auditr index repos

# drop this repo's rows from the shared index
auditr index forget

# act on another checkout instead of the working directory
auditr index list -r ../other-repo

# raw JSON
auditr index list --json
```

## Where the index lives

- One SQLite database for every repo you have ever scanned: `~/.auditor/index.db`.
- `$AUDITOR_HOME` relocates that directory; see [configuration.md](configuration.md).
- Only generated state goes there. Repo-authored input (`.auditor/config.toml`,
  `.auditor/plugins/`, `.auditor/baseline.json`) stays in the repo and is read from it.
- The database runs in WAL mode with a busy timeout, so parallel scans queue instead of failing.

## Repo partitions

- Every row is tagged with a repo key: the resolved absolute path of the project root. Two repos
  never collide inside the one database.
- The root is the nearest directory at or above `-r`/`--root` (default `.`) that holds `.git`,
  `pyproject.toml`, or `.auditor`.
- `repos` is the one subcommand that is not scoped to a repo; it reads the whole registry and
  takes no `--root`.

## What each subcommand does

- `add` registers paths as placeholder rows. They carry no content hash until a scan fills them
  in, so they do not show up in `list` yet.
- `list` returns one row per scanned file: path, content hash, line count, language, role, last
  scan time, per-severity finding counts, and the doc path when one is recorded.
- `repos` lists each registered repo with its name and last-scan time.
- `forget` deletes this repo's registry row. Everything that references it cascades with it: the
  cached files, findings, shapes, and graph rows, and the repo's persistent ignores
  ([ignore.md](ignore.md)). It is not undone by a rescan.

## Populating and pruning

- `scan -i` writes the cache; `aggregate` and `crossfile` read it. See [scan.md](scan.md),
  [aggregate.md](aggregate.md), and [crossfile.md](crossfile.md).
- A scan prunes indexed files under the scanned prefix that no longer exist or are now excluded,
  so a subdirectory scan never evicts files outside it.
- The derived tables are a cache. On a schema-version bump they are dropped and rebuilt by the
  next scan, while the repo registry and the persistent ignores are preserved.
