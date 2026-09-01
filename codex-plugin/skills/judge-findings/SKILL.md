---
name: judge-findings
description: Run auditor and judge its candidate findings — for each, read the evidence, decide fix / skip-directive / dismiss, and summarize the verdicts. Use when asked to audit files, judge findings, or resolve auditor candidates.
paths: "**/*.py, **/*.ts, **/*.tsx"
context: fork
agent: auditor-reviewer
---

Judge the `candidate` findings auditor leaves for you (the `auto` ones are already decided
deterministically — report them, don't re-litigate). Judgment is the whole point of this skill:
a candidate finding is evidence, not a verdict. Read before you decide.

## Steps

1. Scan the scope (arg, else the git working-tree changes):
   - MCP: `scan(path=<scope>)`; CLI: `auditr scan <scope> -f json`.
   - Unfamiliar with the JSON shape, or the payload is huge? Read `references/reading-output.md`
     — the compact vs full shapes, what `rules`/`omitted`/`totals` hold, and how to narrow with
     `severity=`/`rule=`/`limit=`/`detail=`.
2. For each finding with `verdict_kind == "candidate"`, worst-severity-first:
   - MCP path: `scan` defaults to compact output (no `evidence`) — call `finding_detail(file,
     rule_id, line)` first (or re-`scan` with `detail="full"`). CLI JSON already has `evidence`.
   - Read `message`, `evidence`, `suggestion`; open the site at `file:line`.
   - Apply the per-category heuristics in `references/judging.md` — what's a real issue vs a
     false positive for *this* category, and how to verify it (e.g. security: is the sink
     reachable from untrusted input? dead-code: does `graph usages <symbol>` confirm no
     `used_by` before you delete anything?).
   - Decide: **fix** it, **suppress** a true false-positive with a line-anchored
     `# auditor: skip: <RULE-ID>` directive, or **dismiss** with a reason (no code change).
   - The skip directive is anchored to the finding's reported line — for a multi-line statement
     (a wrapped `except (...)`, a decorated `def`) that's the *statement's* keyword line, not
     wherever in the block feels natural. A misplaced directive silently no-ops: it parses fine,
     matches nothing, and the finding keeps firing. See `references/examples.md` for a real
     instance of this happening in this very repo, plus two fully worked fix/dismiss examples.
3. Report `auto` findings as already-decided.
4. End with a verdict summary: counts fixed / suppressed / dismissed, worst remaining.

Severity: `blocking > high > medium > low > suggestion` (`blocking` is the top tier).

## References

- `references/reading-output.md` — JSON shape (compact vs full), annotated real examples,
  narrowing tokens, recovering `evidence` via `finding_detail`.
- `references/judging.md` — per-category real-issue-vs-false-positive heuristics and the
  fix/skip/dismiss decision rule, for every category that actually emits `candidate` findings.
- `references/examples.md` — fully worked real findings: a true-positive fix, a false-positive
  skip-directive, and a live in-repo example of the line-anchoring footgun.
