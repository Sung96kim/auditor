# Query recipes

Source of truth: `auditor/graph/query.py` (`GraphQuery` — `related`/`neighbors`/`search`/
`usages`/`clusters`/`concept`), `auditor/mcp/graph_tools.py` (the `graph_*` MCP tools + docstrings),
`auditor/cli/graph.py` (CLI subcommands), `auditor/graph/detectors.py` (`GRAPH-*` rules). All
example output below is real, captured against this repo (`auditor/graph/query.py` module and
friends) — node ids and counts will differ on yours, but the shape won't.

## Is symbol X dead?

```
auditr graph usages <symbol> --json
```

Read `used_by` — grouped by structural edge kind (`contains`, `calls`, `overrides`, `inherits`,
`imports`, ...), each with a full `count` and a rank-ordered `sample`. A genuinely unused symbol
resolves (has a node) but has an **empty `used_by` dict** — `GraphQuery.usages`'s `summarize()`
only adds a key when at least one edge of that kind exists, so a dead symbol's `used_by` is
literally `{}`, and `total_in` is `0`. Contrast with a real (non-dead) symbol:

```json
{
  "symbol": "find_root",
  "resolved": "auditor/discovery.py::find_root",
  "kind": "function",
  "used_by": {
    "contains": {"count": 1, "sample": ["auditor/discovery.py"]},
    "calls": {"count": 31, "sample": ["auditor/engine.py::audit_target", "auditor/cli/scan.py::_diff_report_only", "..."]}
  },
  "depends_on": {},
  "total_in": 32,
  "total_out": 0
}
```

`used_by.calls.count == 31` (plus the `contains` edge from its own module) — nowhere close to
dead. If you instead get `"used_by": {}, "total_in": 0"`, don't delete yet:

1. **The graph is static and repo-partitioned.** It won't see dynamic dispatch — string-based
   lookups, `getattr`, decorator/plugin registries — or any usage outside this repo. Grep the
   symbol name as a **string literal** across the repo before trusting the empty result:
   `rg '"<symbol>"' .` / `rg "'<symbol>'" .`.
2. Check whether it's a public API entry point (exported, used by a consumer repo you can't see
   from here).
3. Only then treat it as confirmed dead. This mirrors the auto cross-file rule `PY-DEAD-SYMBOL`
   (private module-level symbols with zero repo references) — see `judge-findings`'s
   `references/judging.md` "dead-code" section for the full verdict rubric; this recipe is just
   the query mechanics.

## Blast radius of changing Y

```
auditr graph usages <symbol> --json      # preferred — full counts, no silent truncation
auditr graph neighbors <symbol> --depth 2 --json   # structural neighborhood, hop-labeled
```

`graph_usages`'s own docstring is explicit about which to reach for: "Prefer this over
graph_neighbors for 'how is X used' — neighbors truncates silently with no totals." `neighbors`
is for browsing a local structural neighborhood (each hit tagged with `edge` kind, `direction`
in/out, and `hops`); `usages` is for "how many things break if I change this" — it gives you the
real denominator (`total_in`/`total_out`) plus a sample, so you know whether the sample is the
whole story or a fraction of it.

Real high-blast-radius example from this repo — `GRAPH-GOD-CONCEPT`'s bottleneck detector already
flagged `AuditContext` (103 dependents) and `Finding` (193 dependents) as load-bearing; confirmed
via `usages`:

```
auditr graph usages AuditContext --json
# → total_in: 103 — a change here needs the widest possible test coverage before merging.
```

For a symbol you're about to change: run `usages`, look at `total_in` for a rough blast-radius
number, then skim the `sample` (rank-ordered, so the highest-rank callers surface first) to see
*what kind* of code depends on it — a handful of test fixtures is a different risk profile than
30 production call sites across unrelated modules.

## Hotspots / god-concepts

```
auditr graph clusters --json
```

Lists concept clusters (`cluster_id`, `label`, `member_count`), largest first — a quick "what are
the big cohesive areas of this codebase" overview. Real output, this repo:

```json
[
  {"cluster_id": 0, "label": "path", "member_count": 226},
  {"cluster_id": 1, "label": "database", "member_count": 211},
  {"cluster_id": 2, "label": "ctx", "member_count": 211}
]
```

For *why* something is a hotspot, not just its size, the graph build also runs two advisory
detectors (`GRAPH-*`, always `suggestion` severity, `candidate` verdict — see `auditor/graph/
detectors.py`) whose findings live in the normal finding stream (`scan`/`aggregate`), not a
separate graph command:

- **`GRAPH-GOD-CONCEPT`** — two distinct shapes, same rule id:
  - *high fan-out*: `"{name} has high fan-out (N) — too many responsibilities; consider
    decomposing it."` — this symbol calls/references/contains too much (log-space outlier vs the
    repo's fan-out distribution).
  - *bottleneck*: `"{name} is a bottleneck (N dependents) — changes here have wide
    blast-radius."` — real example from this repo: `"AuditContext is a bottleneck (103
    dependents) — changes here have wide blast-radius."` This is the load-bearing-code signal;
    cross-check the count with `graph usages` (above) before treating a change here as routine.
- **`GRAPH-SCATTERED-CONCEPT`** — a concept cluster whose members are spread across many modules
  instead of living together. Real example: `"concept 'reporters' is scattered across 7 modules
  (9 symbols) — consider consolidating."` Points at fragmentation, the opposite problem from
  god-concept.
- **`GRAPH-NAMING-INCONSISTENCY`** (`style` category) — verbs used inconsistently across
  same-shaped functions; see `judge-findings`'s `references/judging.md` "style" section for the
  verdict rubric.

Pull these via a normal scan/aggregate scoped to the `GRAPH-` prefix, e.g.
`auditr scan . --rule GRAPH-GOD-CONCEPT --rule GRAPH-SCATTERED-CONCEPT -f json` (repeat `--rule`
per id) — or read them straight out of an `aggregate-report` rollup, they show up under
"Candidates to judge" like any other suggestion-severity candidate.

## Locate by name/term

```
auditr graph search <term> --json     # symbol ids containing the term, highest-rank first
auditr graph concept <term> --json    # the concept cluster best matching the term
```

`search` is substring-over-node-id — use it to find the *exact* node id before a `usages`/
`neighbors` call when you're not sure of the precise name (`graph usages` also does its own
fuzzy resolution — exact id, or a `.name`/`::name` suffix match — but ambiguous short names get
reported under `"ambiguous": [...]` in `usages`, so `search` first if you want to pick the right
one deliberately). Real example:

```json
[
  {"id": "auditor/discovery.py::find_root", "kind": "function", "rank": 0.00383},
  {"id": "tests/test_discovery.py::test_find_root", "kind": "function", "rank": 0.0}
]
```

`concept` is coarser — it finds the *cluster* a term belongs to (by label match first, then by
counting members whose name contains the term), and returns the whole membership, not just the
matching node. Use it when you're asking "what part of the codebase handles X" rather than
"where exactly is symbol X."

## Reading the output: `used_by` vs `depends_on`, edge kinds, staleness

- **`used_by`** (incoming structural edges) = who depends on this symbol — what breaks if you
  change it. **`depends_on`** (outgoing) = what this symbol needs — its own dependencies. Both
  are keyed by edge kind, each `{count, sample}`.
- **Structural edge kinds** (`_STRUCTURAL` in `query.py`, what `usages`/`neighbors` walk): `calls`,
  `overrides`, `inherits`, `references_type`, `callback_arg`, `registered_in`, `contains`,
  `imports`. **Semantic edge kinds** (`_SEMANTIC`, what `related` walks instead): `name_similar`,
  `usage_similar` — these are similarity, not "used by"; `related` answers "what's conceptually
  near this" not "what calls this."
- **Ambiguous names**: if a bare name matches several nodes, `usages` picks the highest-rank one
  as `resolved`/`primary` and lists the rest under `ambiguous` — check that list before trusting
  a short/common symbol name resolved to the node you meant.
- **Staleness / when to rebuild**: both `graph build` (CLI) and `graph_build` (MCP, `scan=True`
  default) run a forced incremental scan before building, so a plain rebuild after code changes
  is already fresh — you don't need `--rebuild` for routine "I just edited some files" staleness.
  `--rebuild` (CLI) / re-registering facts is only needed to **discard cached graph facts and
  re-extract from scratch**, which matters after upgrading auditor itself (facts are keyed by
  file content, so an extractor change won't retroactively re-derive facts already cached under
  the same content hash). `graph serve` similarly reuses the existing build unless it's missing
  or `--rebuild` is passed.

## The `[graph]` extra

Graph commands need `numpy`/`scikit-learn`/`networkx`/`snowballstemmer` (the `graph` extra in
`pyproject.toml`). Without it:

- CLI: `auditor/cli/__init__.py` mounts a stub `graph_app` instead
  (`auditor/cli/graph_stub.py`) — every subcommand prints "`auditor graph` requires the optional
  `[graph]` dependencies" and exits 1. Running `auditr graph build` (or any subcommand) is itself
  the check — if it prints that message, the extra isn't installed.
- MCP: `auditor/mcp/graph_tools.py` guards the whole module behind
  `try/except ImportError` — the `graph_*` tools simply aren't registered, so check your
  available-tools list first.

Install: `uv tool install "auditr[graph]"` (matches how the CLI itself is typically installed as
a uv tool; `pip install "auditr[graph]"` / `uv pip install "auditr[graph]"` also work if you're
managing it as a regular dependency instead).

## Visuals: `serve` / `export`

- `auditr graph serve` — opens the interactive graph UI in a browser (`--rebuild` to force a
  fresh scan+build first, `--no-open` to skip auto-opening the tab). CLI-only, no MCP
  equivalent — the MCP tools return data for an agent to reason over, not a browser UI.
- `auditr graph export --format dot|svg [--cluster <id>] [--symbol <name>] [--depth N]` — a
  Graphviz DOT (or rendered SVG, via the system `dot` binary) of the whole graph, one cluster
  (`--cluster`), or a symbol's ego-graph (`--symbol`, `--depth` hops around it). Useful for
  dropping a visual into a PR description or design doc instead of describing the shape in
  prose.
