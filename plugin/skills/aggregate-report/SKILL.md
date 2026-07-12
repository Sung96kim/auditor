---
name: aggregate-report
description: Produce a repo-wide AUDIT.md rollup from auditor's incremental index. Use when you want a full-repo audit summary (distinct from a changeset review).
---

Roll the whole-repo index into `AUDIT.md`.

## Steps

1. Refresh the index: `auditr scan . -i` (incremental — only changed files re-parse). This also
   updates the status line's `.auditor/.status.json`.
2. Aggregate: MCP `aggregate(path=".")` or `auditr aggregate -o AUDIT.md`.
3. The rollup reflects the last incremental scan and is per-repo partitioned in the shared index.
   For a diff-scoped review, use /auditor:audit-changes instead.
