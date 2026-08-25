# Reading `AUDIT.md`

Source of truth: `auditor/aggregate.py` (`AuditAggregator`, `_render`), `auditor/cli/aggregate.py`,
`auditor/mcp/scan_tools.py` (`aggregate` tool), `auditor/database/` (`IndexStore`, `FilesDB`,
`FindingsDB`), `auditor/paths.py`, `auditor/engine.py` (`_scan_cached`, `audit_target`).

## The format

`_render()` builds four sections, in this order:

```markdown
# Audit — consolidated report

Scope: <N> files audited.

**Totals — blocking: <n> · high: <n> · medium: <n> · low: <n> · suggestion: <n>**

## Files with findings (most severe first)

| File | Role | Blocking | High | Medium | Low |
| --- | --- | --- | --- | --- | --- |
| `path/to/file.py` | production | 1 | 0 | 3 | 2 |
...

## Candidates to judge

- **high** `PY-MAL-CREDENTIAL-ACCESS` — reads a known secret/credential path (...)
...
```

- **Scope** — total files with an index row for this repo (not just files with findings).
- **Totals** — repo-wide severity counts, summed across every indexed file's
  `ScanResult.counts`.
- **Files with findings** — despite the header saying "most severe first," the table is actually
  sorted by **raw finding count** (`-len(r.findings)`), not by `ScanResult.severity_key`
  (blocking-first, then count within tier). A file with five `low` findings sorts above a file
  with one `blocking` finding. Don't read table order as a severity ranking — read the Blocking
  column, or re-sort yourself, if you need worst-first.
- **Candidates to judge** — every finding across the whole scope with `verdict_kind == "candidate"`,
  properly sorted worst-first this time (`-severity_rank(f.severity)`, then `rule_id`). This is
  the list `judge-findings` works through — see that skill for the fix/skip/dismiss workflow.
  `suggestion`-tier and `auto`-verdict findings never appear here even if present in the totals.

`suggestion` severity is intentionally the softest tier (`_SEVERITY_ORDER` puts it below `low`)
and is never CI-blocking — expect it to show up in totals but not drive any gate.

## Where the data comes from — no re-scan

`AuditAggregator._results()` does not scan anything. It reads three things straight from the
shared index for this repo's partition:

1. `index.files.list()` — every file this repo has a `files` row for (whatever was scanned and
   cached, ever).
2. `index.findings.grouped()` — every cached finding, grouped by file path.
3. `IgnoreList.from_rows(await index.ignores.list()).filter(results)` — drops findings covered
   by a persistent ignore entry, so the rollup matches what `scan` itself would show.

Because it's a pure cache read, `AUDIT.md` reflects **the last incremental scan of each file
individually** — not a single synchronized snapshot. A file you haven't scanned since a recent
edit shows stale findings; a file you've never scanned at all (no `files` row) doesn't appear in
the rollup at all, even if it exists on disk.

## The incremental index model

- **One shared database**, not one per repo: `~/.auditor/index.db` (override with
  `$AUDITOR_HOME`), covering every repo you've ever scanned on this machine.
- **Partitioned inside the db by `repo`** — every row in every table carries a `repo` column set
  to `repo_key(root)`, the repo's resolved absolute path. `IndexStore.connect(db_path, repo)`
  binds one handle to exactly one repo's rows; two repos never see each other's findings even
  though they share one file on disk. `index.repos` is the cross-repo registry table (name +
  last-scanned) that lists what's in the shared db.
- **Only changed files re-parse.** `ScanEngine._scan_cached` compares the file's current
  `sha256` against the `sha256` recorded on its last scan (`cached_sha != sha`); an unchanged
  file's findings are served straight from the `findings` cache table instead of re-running
  detectors. This is what makes `scan . -i` cheap enough to run before every `aggregate` — a
  whole-repo scan after a small edit only re-parses the files that actually changed.
  `SCHEMA_VERSION` bumps drop and rebuild the cache tables (`files`/`findings`/etc.) but preserve
  `repos`/`ignores` (user state); a plain re-scan repopulates the cache tables from scratch.
- **Incremental mode requires a directory target and the index enabled**
  (`incremental and not no_index and target.is_dir()` in `audit_target`) — a single-file scan or
  `--isolated`/`--no-index` run never touches the shared index, so those files won't show up in
  `aggregate` output no matter how recently you scanned them individually.

## Why `scan -i` before `aggregate`

`aggregate` never scans — it only reads what's already in the index. Run

```bash
auditr scan . -i
```

first so the cache reflects the current state of every file (only the changed ones actually
re-parse), then

```bash
auditr aggregate -o AUDIT.md
```

to render it. Skipping the `-i` scan doesn't error — it just means `AUDIT.md` reflects whatever
was cached from the *last* time each file was scanned, which may be arbitrarily stale or, for a
never-scanned repo, empty. `auditr aggregate --help` states this directly: "Roll up the index
into AUDIT.md (run `scan --incremental` first)."

A directory-level `-i` scan has one more side effect worth knowing about: it writes
`.auditor/.status.json` (gitignored) — the compact severity-count cache the plugin's status line
reads. `AUDIT.md` and `.status.json` are two independent artifacts built from the same
information; keeping `-i` scans current keeps both fresh together.

## `aggregate` vs `/auditor:audit-changes`

Different scope, different purpose — not a "which is better" choice:

- **`aggregate`** — whole-repo posture. No gate, no severity threshold; every cached finding for
  every indexed file, rolled into one document. Use it for a periodic health snapshot or before
  a release, not for reviewing one change.
- **`/auditor:audit-changes`** (the `audit-changes` skill) — diff-scoped, against a base ref
  (`--since`/`--changed`/`--vs-base`), with `fail_on`/`--fail-on` wired to a CI-style gate. The
  whole repo is still scanned so cross-file rules stay correct, but *reported* findings are
  limited to files that actually changed. Use it for PR/CI review, where "did this change
  introduce a new problem" is the actual question, not "what's the repo's overall state."

Both ultimately read from the same underlying scan/index machinery — `aggregate` just skips the
diff-scoping and the gate.
