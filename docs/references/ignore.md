# ignore reference

`auditr ignore` manages persistent suppression of findings for one repo, keyed by rule id and
scoped repo-wide, to a file, or to a single line. `auditr ignore --help` lists every flag. The
rows live in the shared index ([index.md](index.md)), so they apply to every later scan on this
machine, over the CLI and over MCP, and are never committed.

## Common invocations

```bash
# ignore a rule everywhere in this repo
auditr ignore add PY-SEC-WEAK-HASH

# ignore it in one file
auditr ignore add PY-SEC-WEAK-HASH --file src/legacy.py

# ignore one finding, with a note stored alongside it
auditr ignore add PY-SEC-WEAK-HASH --file src/legacy.py --line 42 --reason "vetted"

# accept a rule id the registry does not know yet
auditr ignore add LOCAL-NO-BARE-EXCEPT --force

# validate a rule id contributed by a local plugin before storing it
auditr ignore add LOCAL-NO-BARE-EXCEPT -a

# show the ignores for this repo, with their ids
auditr ignore list

# remove one by the id `list` prints
auditr ignore rm 3
# or by the selector used to add it
auditr ignore rm PY-SEC-WEAK-HASH --file src/legacy.py

# drop every ignore for this repo
auditr ignore clear

# raw JSON
auditr ignore list --json
```

## Scopes and matching

- No scope: the rule is suppressed in every file of the repo.
- `--file`: a path relative to the repo root; the rule is suppressed in that file.
- `--file --line`: one finding. `--line` without `--file` is an error.
- A line-level add snapshots the offending text and stores its hash, so the ignore follows the
  code when lines shift and stops matching once that text changes.
- When no finding exists at the line at add time, the ignore is stored with a literal-line
  fallback and the command says so in its output.
- Re-adding the same rule id, file, and line updates the stored hash and reason instead of
  creating a second row.
- `list` prints the id, rule id, file, line, stored hash, reason, and creation time.

## Rule ids

- `add` loads the repo config first, so rule ids contributed by entry-point and config-named
  plugins validate like built-ins.
- `-a`/`--allow-local-plugins` also loads `.auditor/plugins/*.py` so their rule ids validate; see
  [plugins.md](plugins.md).
- An unrecognized rule id fails with a suggestion. `--force` stores it anyway, for a plugin rule
  that is not loaded in this shell.

## What an ignore changes

- Matched findings are dropped from `scan`, `report`, and `aggregate`, and counted per file as
  `ignored`.
- They are dropped before the CI gate, so they never trip `scan --fail-on`.
- `scan --show-ignored` and `report --show-ignored` keep them in the report. They then count
  toward `--fail-on` again.
- `rm` needs an id from `list`, or the rule id plus the exact `--file`/`--line` used at add time.
  A selector that matches nothing exits non-zero.
- `clear` reports how many rows it removed.
- `index forget` deletes this repo's ignores along with its cached rows, and refuses without
  `-y`/`--yes` while any exist; see [index.md](index.md).

## In-file skip directives

The other way to suppress a finding is a comment in the source, which travels with the repo
instead of living in your local index. A third option, `--baseline`, freezes today's findings as
a committed snapshot; see [scan.md](scan.md).

- `# auditor: skip` suppresses every finding anchored to that line.
- `# auditor: skip: RULE-ID, OTHER-RULE-ID` suppresses only those rule ids on that line.
- `# auditor: skip-file` anywhere in the file suppresses every finding in it.
- `# auditor: skip-file: RULE-ID` suppresses those rule ids file-wide.
- Both `#` and `//` comment markers work, the keyword is case-insensitive, and spacing is
  flexible. Codes are auditor rule ids; a code that matches no rule is inert.
- Plain `# noqa` is not read by the auditor, so ruff and flake8 keep their own namespace.

## Where a skip directive has to sit

- In Python only real comments count: the file is tokenized, so a directive inside a string or
  docstring does nothing.
- In Python a line directive is honored anywhere inside the flagged statement's logical line, so a
  trailing comment after a wrapped signature's closing `):` still suppresses a finding anchored to
  the statement's first line.
- A comment on a line of its own maps to itself and never bleeds into the statement below it.
- Languages with no tokenizer wired up match the raw line text instead.
- A Python file that does not parse suppresses nothing.
- `scan --no-skips` ignores every directive for one run. The config field that turns them off
  permanently is in [configuration.md](configuration.md).
- Suppressed findings are counted per file as `suppressed`, never silently dropped.
