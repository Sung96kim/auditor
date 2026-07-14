# Reading auditor's JSON output

Source of truth: `auditor/reporters/json_reporter.py` (`JsonPayload`), `auditor/models.py`
(`Finding`, `Category`, `VerdictKind`).

## Two shapes

- **compact** (MCP `scan`/`report` default) — rule metadata hoisted into a one-time `rules`
  map, findings slimmed to `rule_id`/`severity`/`line`/`message`, **no `evidence`**, clean files
  omitted, worst-severity files first.
- **full** (CLI `-f json` default) — every field inline per finding, including `evidence` and
  `verdict_kind`. This is the back-compat shape; the CLI never trims it.

There's also **summary** (MCP-only: `totals` + `by_rule`/`by_file` counts, no findings) — for a
top-level gauge before you decide how deep to go.

## Compact shape, annotated

Real output (`scan(path="auditor/languages/python/detectors/oop.py")`, trimmed to one file):

```json
{
  "rules": {
    "PY-OOP-HIGH-COMPLEXITY": {
      "category": "oop-composition",
      "verdict_kind": "candidate",
      "checklist_item": null,
      "standard_refs": [],
      "suggestion": "extract helpers; reduce branching"
    }
  },
  "files": [
    {
      "file": "auditor/languages/python/detectors/oop.py",
      "role": "production",
      "findings": [
        {
          "rule_id": "PY-OOP-HIGH-COMPLEXITY",
          "severity": "low",
          "line": 524,
          "message": "`_field_copies` cyclomatic complexity 18 (> 10)"
        }
      ]
    }
  ],
  "totals": { "blocking": 0, "high": 0, "medium": 0, "low": 3, "suggestion": 0,
              "suppressed": 0, "ignored": 0 },
  "scanned": 1
}
```

What lives where:

- `rules[rule_id]` — the class-level constants, deduped once per rule id (not per finding):
  `category`, `verdict_kind`, `checklist_item`, `standard_refs`, `suggestion`. **`severity` is
  NOT here** — config/role can relax severity per file, so it stays inline on the finding.
- `files[].findings[]` — per-occurrence data only: `rule_id`, `severity`, `line`, `message`,
  and `suggestion` *only if it differs* from the rule-level default in `rules[]`.
- `totals` — repo/scope-wide counts by severity, plus `suppressed` (dropped by an in-file
  `# auditor: skip`) and `ignored` (dropped by a persistent ignore entry).
- `scanned` — total files scanned (compact omits clean files from `files[]`, so this is the
  only place the true file count survives).
- `omitted` (present only when the `limit` cap trims output) — `{findings, files, hint}`; the
  hint tells you how to widen (`severity=`, `rule=`, raise `limit=`, or `detail="full"`).

**`evidence` is never in compact.** You need it to judge a candidate — go get it (see below).

## Full shape, annotated

Real output (`auditr scan <file> -f json --isolated`, one finding):

```json
{
  "rule_id": "PY-OOP-HIGH-COMPLEXITY",
  "category": "oop-composition",
  "severity": "low",
  "verdict_kind": "candidate",
  "line": 524,
  "message": "`_field_copies` cyclomatic complexity 18 (> 10)",
  "evidence": "def _field_copies(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, int]:",
  "suggestion": "extract helpers; reduce branching",
  "checklist_item": null,
  "standard_refs": []
}
```

`evidence` defaults to the stripped source line at `line` (`ctx.line_text(line)`); a handful of
detectors override it with something more specific (e.g. `target_name = '...'` for a hardcoded
secret, never the literal secret value). Either way it's one line — enough to judge from, not a
full diff.

## Narrowing tokens

Both MCP `scan`/`report` and the CLI accept the same narrowing knobs (MCP kwargs / CLI flags):

| MCP kwarg | CLI flag | Effect |
|---|---|---|
| `severity="high"` (or a list) | `-s high` (repeatable) | keep only these severities |
| — | `-m high` | keep this severity **and above** |
| `rule="PY-SEC-SSRF"` (or a list) | `--rule PY-SEC-SSRF` (repeatable) | keep only these rule ids |
| `limit=50` (compact only, default 50) | — | worst-N findings; surplus rolls into `omitted` |
| `detail="compact"\|"full"\|"summary"` | `-f json` is always full | pick the payload shape |
| `since="<git-ref>"` | `--since <ref>` | scope to files changed vs a ref (whole repo still scanned so cross-file rules hold) |

Start with `severity=` or `rule=` to cut a huge scan down before reading anything.

## Recovering evidence from compact output

Compact hides `evidence` to keep token cost down. Two ways to get it back for a specific
finding:

1. `finding_detail(file, rule_id, line)` (MCP) — returns that one finding's full record
   (`evidence`, `suggestion`, `standard_refs`, everything). Cheapest — one finding, not the
   whole file.
2. Re-run with `detail="full"` (MCP) or use the CLI (`-f json` is already full) — pulls
   everything inline, useful when you're about to judge most of a file's findings anyway.

`file`/`rule_id`/`line` for `finding_detail` come straight off the compact finding entry, so you
never have to re-derive them.
