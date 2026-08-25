# Reviewing a changeset

Source of truth: `auditor/gate.py` (gate semantics), `auditor/cli/scan.py` (`--since`/`--changed`/
`--vs-base`/`--fail-on`), `auditor/mcp/scan_tools.py` (`scan` tool's `gate` field), `auditor/discovery.py`
(`default_base_ref`, `git_changed_files`).

## Reading the gate result

The gate is computed differently depending on which surface you're on — don't assume the CLI
gives you a JSON field to read.

- **MCP** (`scan(since=<base>, fail_on="high")`): the payload gets a `gate` key —
  `{"fail_on": "high", "tripped": <bool>}`. Read `data["gate"]["tripped"]` directly.
- **CLI** (`auditr scan . --since <base> -f json --fail-on high`): there is **no `gate` key** in
  the JSON at all — `-f json` payload is just `{files, totals}`. `--fail-on` only changes the
  process exit code (`typer.Exit(1)` if tripped). Check `$?`, not the payload.

**Gate counts `auto` findings only** (`auditor/gate.py`):

```python
def gate_tripped(results: list[ScanResult], fail_on: str) -> bool:
    floor = severity_rank(check_severity(fail_on))
    return any(
        f.verdict_kind == VerdictKind.AUTO and severity_rank(f.severity) >= floor
        for r in results
        for f in r.findings
    )
```

`candidate` findings never trip it, no matter how severe. This is real, not a hypothetical —
running `auditr scan . --since main -f json --fail-on high` against this repo's own working
changes returned exit code `0` even though `totals.high == 5`, because all 5 were
`PY-ASYNC-SYNC-IO` findings with `"verdict_kind": "candidate"` (async I/O in a test, not an
auto-decided rule). **A clean gate does not mean a clean changeset** — a diff can carry real
`high`/`blocking` candidates that need your judgment and still exit 0. Always look at `totals`
(or the full finding list) in addition to the gate, and don't report "gate passed" as "no
issues."

## Severity triage order

`blocking > high > medium > low > suggestion` (`blocking` is the top tier — there's no
"critical"). Within a changeset:

1. **`auto` findings first** — these are already decided deterministically (typing, secrets,
   most of `malware`'s unambiguous patterns) and are exactly what the gate is watching. Report
   them as-is; they're not up for debate.
2. **`candidate` findings next, worst-severity-first** — these need judgment before you can call
   the changeset clean. See `judge-findings`'s `references/judging.md` for the per-category
   real-issue-vs-false-positive heuristics; this skill doesn't re-derive them.
3. Within the same severity, prioritize by **file role and blast radius** over raw count — one
   `high` in `auditor/gate.py` (production, load-bearing) outranks five `low`s in a test fixture.
   Use `graph usages <symbol>` (see `explore-graph`) when you need to know whether a changed
   symbol is widely depended-on before deciding how much scrutiny it earns.

## When to hand depth to the auditor-reviewer subagent

Judging every `candidate` finding inline floods the main conversation. The `auditor-reviewer`
subagent (`plugin/agents/auditor-reviewer.md`) does the finding-by-finding triage in an isolated
context and returns a compact report — same tool it uses for `judge-findings`, dispatched
manually here instead of via that skill's `context: fork`.

- **"Keep working" flow** (you're mid-task and the changeset review is a side check, or CI isn't
  blocking on it yet) — dispatch it **in the background**, keep doing other work, and report
  when it lands.
- **Need the verdict now** (a CI gate step, or you're about to merge/hand off and the changeset
  review is the blocking step) — dispatch it **in the foreground** and wait for the result before
  proceeding.

Per the agent's own frontmatter: "When dispatched directly (e.g. `@auditor-reviewer`), you run in
the background by default" — so foreground is something *you* have to ask for explicitly when the
verdict is on the critical path.

## `--since` scans the whole repo, reports the diff

`--since`/`--changed`/`--vs-base` only **scope what's printed** — they don't shrink what's
scanned:

- `--since <ref>`: changed vs an explicit git ref.
- `--changed`: changed vs `HEAD` (working-tree changes).
- `--vs-base`: changed vs the repo's configured `[tool.auditor] diff_base`, falling back to
  `default_base_ref` — the first of `main`/`master`/`develop`/`development` (local or
  `origin/`) that resolves. Fails loudly if none exist and no `diff_base` is configured.

From `auditor/discovery.py`'s `git_changed_files` docstring: "each is still audited in full, and
the whole repo is still scanned (cheaply, via the cache) so cross-file rules stay correct." The
CLI actually forces this: `auditor/cli/scan.py` sets `incremental = True` whenever a diff mode is
requested (unless `--no-index`), so the full-repo pass reuses the on-disk cache and stays fast —
you're not paying a full rescan cost for the correctness the whole-repo pass buys (cross-file
dedup, `PY-DEAD-SYMBOL`, `GRAPH-*` rules all need repo-wide context to be accurate; scoping the
*scan* itself to just the diff would silently break those).

Resolve the base ref in this order: the repo's configured `diff_base`, else the auto-detected
default branch, else fail and ask the user for `--since <ref>` explicitly.
