---
name: audit-changes
description: Audit only what changed against a base ref and gate it — for PR/CI review. Triage by severity, optionally dispatch the auditor-reviewer subagent, and emit AUDIT.md or a PR review.
paths: "**/*.py, **/*.ts, **/*.tsx"
---

Review a changeset the way CI would.

## Steps

1. Resolve the base ref: the repo's configured `diff_base`, else `main`.
2. Scan the diff with the gate:
   - MCP: `scan(since=<base>, fail_on="high")` — read `gate.tripped`.
   - CLI: `auditr scan . --since <base> -f json --fail-on high` — non-zero exit = gate tripped.
3. Triage findings by severity. For depth on `candidate` findings, dispatch the
   **auditor-reviewer** subagent:
   - "Keep working" flow → run it in the **background**, report when it lands.
   - Need the verdict now (CI gate) → run it **foreground**.
4. Emit an `AUDIT.md` or a PR review body: gate result first, then findings worst-first.

Gate counts `auto` findings only; candidates never fail CI.
