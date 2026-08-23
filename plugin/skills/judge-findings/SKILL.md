---
name: judge-findings
description: Run auditor and judge its candidate findings — for each, read the evidence and return one verdict: fix-recommended, suppress-recommended, or dismiss. Reports and recommends; never edits the audited code. Use when asked to audit files, judge findings, or resolve auditor candidates.
paths: "**/*.py, **/*.ts, **/*.tsx"
context: fork
agent: auditor-reviewer
---

Judge the `candidate` findings auditor leaves for you (the `auto` ones are already decided
deterministically — report them, don't re-litigate). Judgment is the whole point of this skill:
a candidate finding is evidence, not a verdict. Read before you decide.

This skill reports and recommends. It does not edit the audited source, does not write a skip
directive into a file, and does not call `ignore_add` — unless the user explicitly asks you to
apply a recommendation.

## Steps

1. Scan the scope (arg, else the git working-tree changes):
   - MCP: `scan(path=<scope>)`; CLI: `auditr scan <scope> -f json`.
   - Unfamiliar with the JSON shape, or the payload is huge? Read `references/reading-output.md`
     — the compact vs full shapes, what `rules`/`omitted`/`totals` hold, and how to narrow with
     `severity=`/`rule=`/`limit=`/`detail=`.
2. Order the `candidate` findings before you judge them. Severity is a risk ordering, not a
   value ordering:
   - Risk categories first, at `blocking`/`high`: `security`, `malware`, `secrets`,
     `supply-chain`, `correctness`, `async`, `a11y`.
   - Then the promoted maintainability categories: `oop-composition`, `dead-code`, `typing`,
     `testing`, `config`, `style`, `design-system`, `react`. Every candidate in them earns its own
     recommendation — they are never rolled up into a "+N lower" count, and never dismissed on
     tier alone. `references/judging.md` carries the dismissal bar.
   - Then everything else, worst-severity-first.
3. For each candidate, in that order:
   - MCP path: `scan` defaults to compact output (no `evidence`) — call `finding_detail(file,
     rule_id, line)` first (or re-`scan` with `detail="full"`). CLI JSON already has `evidence`.
   - Read `message`, `evidence`, `suggestion`; open the site at `file:line`.
   - Apply the per-category heuristics in `references/judging.md` — what's a real issue vs a
     false positive for *this* category, and how to verify it (e.g. security: is the sink
     reachable from untrusted input? dead-code: does `graph usages <symbol>` confirm no
     `used_by` before you recommend a deletion?).
   - Land on exactly one verdict:
     - **fix-recommended** — say what to change, where (`file:line`), and why; add a short
       snippet when the wording alone won't carry the change.
     - **suppress-recommended** — quote the directive and the line it belongs on
       (`# auditor: skip: <RULE-ID>` with a short parenthetical reason), or the db-backed
       equivalent: `auditr ignore add <RULE-ID> --file <path> --line <n> --reason "<why>"`.
     - **dismiss** — a false positive not worth a permanent marker; state the reason.
   - In a promoted category, the recommendation also names the extensibility payoff — what
     becomes possible or cheaper once it lands: a second consumer drops in, a boundary becomes
     typed, duplication collapses to one place.
   - Name the directive's line, don't place it. It anchors to the finding's reported line — for
     a multi-line statement (a wrapped `except (...)`, a decorated `def`) that's the
     *statement's* keyword line, not wherever in the block feels natural. A misplaced directive
     silently no-ops: it parses fine, matches nothing, and the finding keeps firing. See
     `references/examples.md` for a real instance of this happening in this very repo, plus two
     fully worked recommendations.
4. Report `auto` findings as already-decided.
5. End with a verdict summary:
   - Counts recommended-fix / recommended-suppress / dismissed, and the worst remaining.
   - A **Maintainability recommendations** section, listed separately from the risk findings, with
     one entry per promoted-category candidate.

   The recommendations are the deliverable — apply none of them yourself.

Severity: `blocking > high > medium > low > suggestion` (`blocking` is the top tier).

## References

- `references/reading-output.md` — JSON shape (compact vs full), annotated real examples,
  narrowing tokens, recovering `evidence` via `finding_detail`.
- `references/judging.md` — per-category real-issue-vs-false-positive heuristics and the
  fix-recommended / suppress-recommended / dismiss decision rule, for every category that
  actually emits `candidate` findings.
- `references/examples.md` — fully worked real findings: a recommended fix, a recommended
  suppression, and a live in-repo example of the line-anchoring footgun.
