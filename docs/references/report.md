# report reference

`report` audits exactly one file with no index and no cross-file pass, and renders the result in
a machine format (JSON by default). `auditr report --help` lists every flag. The argument must be
an existing file; nothing else is required.

## Common invocations

```bash
# findings for one file as JSON on stdout
auditr report path/to/file.py

# markdown instead
auditr report path/to/file.py -f md

# write an HTML report to a file
auditr report path/to/file.py -f html -o report.html

# audit the file against a different profile
auditr report path/to/file.py -p strict

# include findings that persistent ignores would hide
auditr report path/to/file.py --show-ignored
```

- `-f` defaults to `json` here, unlike `scan`, which prints a human summary when no format is
  asked for.
- A bad `-f` fails before the file is read and lists the accepted formats.
- `-o PATH` writes the report to that path and confirms on stderr; without it the report goes to
  stdout.
- `--config-json` merges a JSON object over the resolved config as the highest layer. See
  [configuration.md](configuration.md).

## Single-file audit

- Stateless: the index is never written, so no cross-file findings appear (duplicate models and
  functions, dead symbols, scattered settings, unused fixtures, private symbols used elsewhere).
- For those, run `auditr scan <file>`, which opens the shared index and runs the cross-file pass
  against the repo's already-recorded shapes. See [scan.md](scan.md).
- Config resolution, profile selection, role classification, and in-source `# auditor: skip`
  directives behave exactly as in `scan`.
- Persistent ignores still apply. The shared index is opened only when it already exists, so a
  first run on a fresh machine creates nothing. See [ignore.md](ignore.md).
- No status file is written; see [scan.md](scan.md) for which runs update it.
- The AST class and function manifest is a separate command: [manifest.md](manifest.md).

## Evidence

- Every finding carries `evidence`, the offending source text, alongside `message`, `suggestion`,
  `checklist_item`, and `standard_refs`.
- `evidence` is what a baseline fingerprint hashes, which is why baselined findings survive line
  moves but not a rewrite of the offending code.
- `verdict_kind` separates the two kinds of finding: `auto` is decided deterministically by the
  tool, `candidate` is evidence for an agent to judge. See [rules.md](rules.md).

## The JSON shape

- Top level: `files` and `totals`.
- Each entry in `files`: `file`, `language`, `role`, `cached`, `counts` keyed by severity,
  `suppressed`, `ignored`, `findings`, `skipped_rules`.
- Each entry in `findings`: `rule_id`, `category`, `severity`, `verdict_kind`, `line`, `message`,
  `evidence`, `suggestion`, `checklist_item`, `standard_refs`.
- Each entry in `skipped_rules`: `rule_id` and the `reason` it did not run.
- `totals` sums the severities and adds `suppressed` and `ignored`.
- `-f sarif` renders the same findings as SARIF 2.1.0, using the baseline fingerprint as the
  partial fingerprint so a dashboard can track a finding across line moves.
- The MCP `report` tool returns a compacted variant of this data. See
  [auditr-mcp.md](auditr-mcp.md).
