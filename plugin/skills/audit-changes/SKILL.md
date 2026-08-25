---
name: audit-changes
description: Audit only what changed against a base ref and gate it — for PR/CI review. Triage by severity, optionally dispatch the auditor-reviewer subagent, and emit AUDIT.md or a PR review.
paths: "**/*.py, **/*.ts, **/*.tsx"
---

Review a changeset the way CI would: scope to what changed, read the gate honestly, triage what's
left, and hand off depth instead of drowning the conversation in findings.

## Steps

1. Resolve the base ref: the repo's configured `[tool.auditor] diff_base`, else the
   auto-detected default branch (`main`/`master`/`develop`/`development`), else ask for an
   explicit ref. `--vs-base` does this resolution for you; `--since <ref>` skips straight to an
   explicit ref.
2. Scan the diff with the gate:
   - MCP: `scan(since=<base>, fail_on="high")` — read `data["gate"]["tripped"]`.
   - CLI: `auditr scan . --since <base> -f json --fail-on high` — **no `gate` key in the JSON**;
     check the process exit code instead (non-zero = tripped).
   - Either way: the gate counts **`auto` findings only** — a diff full of high-severity
     `candidate` findings can still show a passing gate. Don't report "gate passed" as "changeset
     is clean" without also checking `totals`. Unfamiliar with this, or need the triage order and
     the background-vs-foreground call on the auditor-reviewer subagent? Read
     `references/triage.md`.
3. Triage findings: `auto` first (already decided), then `candidate`s worst-severity-first. For
   depth on a large batch of candidates, dispatch the **auditor-reviewer** subagent rather than
   judging inline — background if this is a "keep working" check, foreground if you need the
   verdict now (e.g. blocking a merge). `references/triage.md` covers the decision and how
   `--since` still scans the whole repo (for cross-file rule correctness) while only reporting
   the diff.
4. Emit an `AUDIT.md` or a PR review body: gate result first, then findings worst-severity-first,
   grouped by file, with `rule_id` + line + a one-line judgment for each candidate. `AUDIT.md` is
   a real reporter output (`-f md`) — don't hand-assemble it. A PR review body isn't a built-in
   format — assemble it from the JSON payload per the template in `references/output-formats.md`.

Severity: `blocking > high > medium > low > suggestion` (`blocking` is the top tier).

## References

- `references/triage.md` — reading the gate result on both surfaces (and why a passing gate
  isn't "clean"), severity triage order, when to dispatch auditor-reviewer in the background vs
  foreground, prioritizing across many changed files, and why `--since`/`--changed`/`--vs-base`
  still scan the whole repo.
- `references/output-formats.md` — the real `AUDIT.md` shape (`-f md`, annotated) and a
  from-scratch PR-review-body template, both with real-shaped examples from this repo.
