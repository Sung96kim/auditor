---
name: judge-findings
description: Run auditor and judge its candidate findings — for each, read the evidence, decide fix / skip-directive / dismiss, and summarize the verdicts. Use when asked to audit files, judge findings, or resolve auditor candidates.
paths: "**/*.py, **/*.ts, **/*.tsx"
context: fork
agent: auditor-reviewer
---

Judge the `candidate` findings auditor leaves for you (the `auto` ones are already decided).

## Steps

1. Scan the scope (arg, else the git working-tree changes):
   - MCP: `scan(path=<scope>)`; CLI: `auditr scan <scope> -f json`.
2. For each finding with `verdict_kind == "candidate"`, worst-severity-first:
   - MCP path: `scan` defaults to compact output (no `evidence`) — call `finding_detail(file,
     rule_id, line)` first (or re-`scan` with `detail="full"`). CLI JSON already has `evidence`.
   - Read `message`, `evidence`, `suggestion`; open the site at `file:line`.
   - Decide: **fix** it, **suppress** a true false-positive with a line-anchored
     `# auditor: skip: <RULE-ID>` directive, or **dismiss** with a reason.
   - The skip directive is anchored to the finding's reported line (the `def` line for
     signature rules). A misplaced directive silently no-ops — put it on the exact reported line.
3. Report `auto` findings as already-decided.
4. End with a verdict summary: counts fixed / suppressed / dismissed, worst remaining.

Severity: `blocking > high > medium > low > suggestion` (`blocking` is the top tier).
