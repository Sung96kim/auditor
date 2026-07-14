---
name: explore-graph
description: Explore the semantic code graph — dead code, call/dependency impact, symbol usages (find-refs/go-to-def), clusters. Use when tracing how code connects or whether a finding matters.
paths: "**/*.py, **/*.ts, **/*.tsx"
---

Answer structural questions with auditor's semantic graph. Requires the `[graph]` extra
(`uv tool install "auditr[graph]"`); check first and guide install if missing — CLI without it
prints "requires the optional [graph] dependencies" and exits 1, MCP without it simply won't
register the `graph_*` tools.

## Steps

1. Build once: MCP `graph_build()` or `auditr graph build`. Both auto-scan first by default, so a
   plain rebuild after code changes is already fresh; `--rebuild`/`--no-scan` are for the
   discard-cached-facts and skip-scan edge cases, not routine use.
2. Query (MCP `graph_*` tools, else the CLI):
   - `graph usages <symbol>` — who uses it (`used_by`, full counts) and what it depends on
     (`depends_on`); this is your find-references / go-to-definition, and the right tool for "how
     is X used" (prefer it over `neighbors`, which truncates silently with no totals).
   - `graph related` / `graph neighbors` — nearby code: `related` walks semantic (name/usage
     similarity) edges, `neighbors` walks structural (calls/overrides/...) edges by hop depth.
   - `graph clusters` — cohesive concept groups; combine with the `GRAPH-GOD-CONCEPT` /
     `GRAPH-SCATTERED-CONCEPT` findings for *why* something is a hotspot, not just its size.
   - `graph search` / `graph concept` — locate by name/term: `search` finds the exact symbol id,
     `concept` finds the cluster a term belongs to.
   - Unfamiliar with any of these, or need real command + output examples? Read
     `references/recipes.md` — it walks "is X dead", "blast radius of changing Y", "hotspots",
     and "locate by name/term" as concrete recipes, plus how to read `used_by`/`depends_on`, edge
     kinds, and when a rebuild is actually needed.
3. For a visual, `auditr graph serve` opens the browser UI (CLI only); `auditr graph export`
   renders a Graphviz DOT/SVG of the graph, a cluster (`--cluster`), or a symbol's ego-graph
   (`--symbol --depth`).
4. Use `usages` when judging whether a finding matters — dead code (`used_by` empty, confirmed
   with a string-literal grep for dynamic dispatch before deleting anything) vs. widely-used
   (high `total_in`, wide blast radius).

## References

- `references/recipes.md` — concrete query recipes with real commands and real output from this
  repo: dead-code check, blast-radius check, hotspot/god-concept hunting, name/term lookup, how
  to read `used_by` vs `depends_on` and edge kinds, staleness/rebuild rules, and `serve`/`export`
  for visuals.
