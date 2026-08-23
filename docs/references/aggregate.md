# aggregate reference

`aggregate` rolls the shared index up into a single consolidated `AUDIT.md`. It reads cached
findings only and never re-scans. `auditr aggregate --help` lists every flag. Run
`auditr scan --incremental` first so the index has something to roll up.

## Common invocations

```bash
# populate the index
auditr scan . -i
# write AUDIT.md in the current directory
auditr aggregate .

# write the rollup somewhere else (missing directories are created)
auditr aggregate . -o reports/AUDIT.md
```

- `TARGET` only resolves the project root; the rollup always covers everything the index holds
  for that repo, not just the path you pass.
- `-o` defaults to `AUDIT.md` in the current working directory and overwrites an existing file.
  Missing parent directories are created.
- With an empty or missing index the rollup still writes, reporting zero files.

## What AUDIT.md contains

- A scope line with the number of files the index holds for this repo.
- A totals line with the per-severity finding counts across those files.
- A table of the files that have findings, with role and per-severity counts.
- A "Candidates to judge" list of every `candidate` finding, worst severity first, so an agent
  has one place to work from.

## Scope and freshness

- The report reflects the last scan of each file. A file audited before a rule was enabled keeps
  its older result until it is re-scanned.
- Deleted files are pruned from the index by the next scan of their scope, so they stop appearing
  in the rollup at that point. See [scan.md](scan.md).
- Persistent ignores are applied here too, so the counts match what `scan` shows. See
  [ignore.md](ignore.md).
- The index is per repo inside one shared database; `auditr index repos` lists what is in it. See
  [index.md](index.md).
- The same rollup is available over MCP as the `aggregate` tool. See
  [auditr-mcp.md](auditr-mcp.md).
