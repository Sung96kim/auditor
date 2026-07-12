---
name: explore-graph
description: Explore the semantic code graph — dead code, call/dependency impact, symbol usages (find-refs/go-to-def), clusters. Use when tracing how code connects or whether a finding matters.
paths: "**/*.py, **/*.ts, **/*.tsx"
---

Answer structural questions with auditor's semantic graph. Requires the `[graph]` extra
(`uv tool install "auditr[graph]"`); check first and guide install if missing.

## Steps

1. Build once: MCP `graph_build()` or `auditr graph build`.
2. Query (MCP `graph_*` tools, else the CLI):
   - `graph usages <symbol>` — who uses it (`used_by`) and what it depends on (`depends_on`);
     this is your find-references / go-to-definition.
   - `graph related` / `graph neighbors` — nearby code.
   - `graph clusters` — hotspots and cohesive groups.
   - `graph search` / `graph concept` — locate by name/term.
3. For a visual, `auditr graph serve` opens the browser UI (CLI only).
4. Use `usages` when judging whether a finding matters — dead code vs. widely-used.
