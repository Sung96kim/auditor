# Emitting AUDIT.md or a PR review body

Source of truth: `auditor/reporters/markdown_reporter.py` (`MarkdownReporter`, what `-f md`
actually renders), `auditor/aggregate.py` (`AuditAggregator`, the repo-wide rollup — see
`aggregate-report` for that flow; this skill's `AUDIT.md` is diff-scoped, not the same command).

## AUDIT.md — real reporter output, not hand-assembled

`auditr scan . --since <base> -f md` (or `-o AUDIT.md`) already renders exactly this shape — you
don't need to build it by hand. Real output, captured from this repo's own working-tree diff vs
`main` (trimmed to two files):

```markdown
# Audit report

**Totals — blocking: 0 · high: 5 · medium: 6 · low: 41**

## Files with findings

| File | Role | Blocking | High | Medium | Low |
| --- | --- | --- | --- | --- | --- |
| `tests/test_mcp_server.py` | test | 0 | 5 | 0 | 6 |
| `plugin/hooks/audit_edit.py` | script | 0 | 0 | 2 | 5 |

### `tests/test_mcp_server.py`

- 🔎 **high** `PY-ASYNC-SYNC-IO` (L555) — sync `subprocess.run(...)` blocks the event loop inside async `test_scan_since_head`
- 🔎 **low** `PY-TEST-DUPLICATE-SETUP` (L121) — 5 tests share the same 2-statement setup (...) — extract a fixture

### `plugin/hooks/audit_edit.py`

- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L39) — `_report` returns `dict | None` (bare dict = dict[Any, Any]); return a typed model
- 🔧 **low** `PY-CONFIG-ADHOC-ENV` (L88) — ad-hoc env read; move to a BaseSettings field
```

What each part is, straight from `MarkdownReporter`:

- `totals_line` — repo-scope totals (not diff-scope; same as any `-f md`/`-f json` run).
- `summary_table` — files sorted by `r.severity_key` (worst file first), one row each, always
  present even if empty.
- `file_section` per flagged file — findings sorted `(-severity_rank, line)`, so worst-first
  *within* the file too, ascending by line as a tiebreaker.
- The mark is the verdict, not a decoration: **🔧 = `auto`** (already decided — the gate watches
  these), **🔎 = `candidate`** (needs your judgment — see `judge-findings`). A file with only 🔎
  marks can still be a clean gate (see `references/triage.md`) — don't read "AUDIT.md has
  entries" as "the gate would trip."

If you're asked for "an AUDIT.md", run the command — don't reconstruct the markdown from JSON by
hand; the reporter is the source of truth for the shape and will drift if you hand-roll it.

## PR review body — assembled, not a built-in reporter

There's no `-f pr` flag; a PR review comment needs a judgment column (real / false-positive, one
line) that no reporter emits, since that requires reading `evidence` and deciding — the reporters
only render what the scan already knows. Build it from the JSON payload (MCP `scan(since=<base>,
fail_on=<floor>, detail="full")`, or CLI `-f json`) after you've triaged per
`references/triage.md`. Template — gate result first, then findings worst-severity-first, grouped
by file:

```markdown
## Auditor review — vs `main`

**Gate: PASSED** (fail-on: high, 0 auto findings ≥ high)
5 `high` and 6 `medium` candidate findings need review — see below.

### tests/test_mcp_server.py

- 🔎 **high** `PY-ASYNC-SYNC-IO` L555 — sync `subprocess.run(...)` blocks the event loop inside
  async `test_scan_since_head`.
  **Judgment:** real — this test function is `async def` and calls `subprocess.run` directly
  instead of `asyncio.to_thread`; not a false positive, low-risk fix.

### plugin/hooks/audit_edit.py

- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` L39 — `_report` returns `dict | None`; return a typed
  model. (auto — already decided, gate-relevant.)

---
_Scope: diff vs `main`, whole repo scanned for cross-file correctness. Candidates need a human/
agent verdict; auto findings are gate-enforced._
```

Rules for filling it in:

- **Gate line first**, in plain language: PASSED/TRIPPED, the `fail_on` floor, and *why* (count
  of `auto` findings at/above the floor) — not just the raw boolean, since "gate passed" reads as
  "nothing to worry about" if you don't also say what it didn't check (candidates).
- **Worst severity first**, both across files and within a file (mirrors the reporter's own
  sort — don't invent a different order).
- **Group by file** — a reviewer scanning a PR thinks per-file, not per-rule.
- Every line needs `rule_id` + `line` + **the one-line judgment** for candidates (real /
  false-positive-dismissed / false-positive-suppressed, with the reason) — a bare finding list
  without judgment isn't a review, it's a re-post of the scan. `auto` findings don't need a
  judgment line (they're already decided); a short "(auto)" tag is enough context.
- Keep the evidence/reasoning as short as what you'd actually want to read in a PR thread — this
  isn't `references/examples.md`-length worked reasoning, it's the verdict plus the one line that
  justifies it.
