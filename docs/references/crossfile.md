# crossfile reference

`crossfile` recomputes the repo-level cross-file findings from the shapes the index already
holds, and prints how many it produced. `auditr crossfile --help` lists every flag. Run
`auditr scan --incremental` first; the pass reads the index and does not parse any source.

## Common invocations

```bash
# populate the index
auditr scan . -i
# recompute the cross-file pass from it
auditr crossfile .

# machine-readable count
auditr crossfile . --json
```

- `TARGET` only resolves the project root; the pass always covers the whole repo partition.
- Output is a single count, `{"cross_file_findings": N}` in JSON.

## What the pass computes

- Duplicate Python models and functions, and duplicate TypeScript components, functions, and JSX
  blocks, grouped only within the same file role so production and test code never pair up.
- Settings classes scattered across modules instead of living together.
- pytest fixtures defined but never referenced.
- Module-level symbols defined but never referenced anywhere in the repo.
- That dead-symbol rule (`PY-DEAD-SYMBOL`) exempts definitions in an `__init__.py`, files outside
  the `production` and `script` roles, and the framework globals `revision`, `down_revision`,
  `branch_labels`, `depends_on` and `pytestmark`, which a framework reads without ever naming
  them.
- Private symbols used from outside the module that defines them.
- Rule ids for all of these are listed by `auditr rules list`. See [rules.md](rules.md).

## When to run it

- Rarely on its own: `scan` runs the same pass automatically whenever an index is present, and
  runs it in memory when there is none.
- Use it to re-derive the findings after editing the settings that drive them, without paying for
  a re-scan.
- Each run clears the previous cross-file findings and rewrites them, so `aggregate` and the next
  `scan` see the new set.
- Grouping reads the shapes table, so a file takes part only after it has been scanned at least
  once with `--incremental`.
- `scan` also exempts symbols named by `pyproject.toml` entry points from the dead-symbol rule; a
  standalone `crossfile` run does not, so it can report symbols a scan would leave alone.
