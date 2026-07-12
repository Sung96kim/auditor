---
name: aggregate-report
description: Produce a repo-wide AUDIT.md rollup from auditor's incremental index. Use when you want a full-repo audit summary (distinct from a changeset review).
---

Roll the whole-repo index into `AUDIT.md` — a whole-repo posture snapshot, not a diff review.

## Steps

1. Refresh the index: `auditr scan . -i` (incremental — only files whose content actually
   changed re-parse; unchanged files are served from cache). Directory-scoped only — a
   single-file or `--isolated` scan never touches the shared index. This also updates the status
   line's `.auditor/.status.json`.
2. Aggregate: MCP `aggregate(path=".")` or `auditr aggregate -o AUDIT.md`. This is a pure cache
   read — no re-scan — so skipping step 1 doesn't error, it just means the rollup reflects
   whatever was cached from each file's *last* scan (arbitrarily stale, or empty for a
   never-scanned repo).
3. Read the rollup: totals by severity, a files-with-findings table, and a "Candidates to judge"
   section (every `candidate`-verdict finding across the whole scope, worst-first) that feeds
   `judge-findings`. See `references/reading-audit-md.md` for the exact format — including one
   gotcha (the files table sorts by raw finding count, not severity, despite its header).
4. The rollup is per-repo partitioned in the one shared `~/.auditor/index.db` — two repos never
   mix rows. For a diff-scoped, gated review instead of a whole-repo snapshot, use
   `/auditor:audit-changes`.

## References

- `references/reading-audit-md.md` — the `AUDIT.md` section-by-section format, where the data
  comes from (no re-scan, per-file cache reads), the incremental index model (shared db,
  per-repo partitioning, sha-based re-parse skip), why `-i` has to run first, and when to reach
  for `aggregate` vs `/auditor:audit-changes`.
