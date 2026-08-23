# graph reference

`auditr graph` builds and queries a semantic graph of a repo: nodes are modules, classes and
functions, edges are structural relations (calls, overrides, imports) and semantic ones (name and
usage similarity). `auditr graph <subcommand> --help` lists every flag. It needs the `[graph]` extra
(`uv tool install "auditr[graph]"`); without it every subcommand prints a message naming the missing
dependencies (numpy, scikit-learn, networkx) and exits 1.

## Common invocations

```bash
# build the graph; auto-scans first to extract facts
auditr graph build .

# who uses a symbol and what it depends on, with full counts
auditr graph usages ComponentBlueprint .

# structural neighbors two hops out
auditr graph neighbors get_user . --depth 2

# nearest semantic neighbors (name and usage similarity)
auditr graph related get_user .

# find the exact symbol id by substring, highest rank first
auditr graph search Blueprint .

# the concept cluster matching a term
auditr graph concept tenant .

# every concept cluster with its label and size
auditr graph clusters .

# interactive UI on a local port
auditr graph serve .

# Graphviz DOT on stdout
auditr graph export . --format dot > graph.dot
```

- `TARGET` defaults to `.` on every subcommand and is resolved to the repo root.
- Every query subcommand takes `--json` for the raw payload. `serve` and `export` do not.

## Building

- `graph build` runs an incremental scan with graph fact extraction forced on, then builds nodes,
  edges and clusters. Nothing has to be enabled in config first.
- `--no-scan` builds from cached facts only. Use it when a scan just ran and nothing changed since.
- `--rebuild` discards the cached graph facts and re-extracts from scratch. Facts are keyed by file
  content, so an extractor change does not invalidate facts already cached under the same hash; run
  `--rebuild` after upgrading auditor.
- Routine staleness needs neither flag: the default auto-scan already picks up edited files.
- Setting `enabled = true` under `[tool.auditor.graph]` also makes a plain `auditr scan -i` populate
  graph facts. See [configuration.md](configuration.md).
- The build runs the `GRAPH-*` detectors, described below.

## Querying

- `usages` groups a symbol's edges by kind into `used_by` (incoming: who depends on it) and
  `depends_on` (outgoing: what it needs). Each group carries a full `count` and a rank-ordered
  `sample`; `total_in` and `total_out` are the true totals. `--sample` sets the sample size (default
  5).
- Reach for `usages` as the find-references and blast-radius query. `neighbors` truncates without
  reporting totals.
- An empty `used_by` with `total_in` of 0 makes a symbol a dead-code candidate, not a confirmed one.
  The graph is static and repo-partitioned, so it misses dynamic dispatch and out-of-repo callers;
  grep the name as a string literal before deleting anything.
- Bare names resolve fuzzily: the highest-rank match becomes `resolved`, the rest are listed under
  `ambiguous`. Run `search` first when a short name could match several nodes.
- `neighbors` walks structural edges (`calls`, `overrides`, `inherits`, `references_type`,
  `callback_arg`, `registered_in`, `contains`, `imports`) out to `--depth` hops, tagging each hit
  with its edge kind, direction and hop count.
- `related` walks semantic edges (`name_similar`, `usage_similar`) instead, so it answers "what is
  conceptually near this", not "what calls this". `--limit` defaults to 10.
- `search` matches a substring against node ids, highest rank first. `--limit` defaults to 20.
- `concept` returns the whole membership of the cluster a term belongs to, matching the cluster
  label first and member names second.
- `clusters` lists every concept cluster with its id, label and member count.
- Worked recipes with real command output live in the plugin's
  [explore-graph recipes](../../plugin/skills/explore-graph/references/recipes.md).

## Graph findings

- The three `GRAPH-*` detectors run during `graph build`. Their findings are `suggestion` severity
  with a `candidate` verdict, and they surface in the normal finding stream
  (`auditr scan`, `auditr aggregate`), not in a graph subcommand.
- `GRAPH-GOD-CONCEPT`: a concept hub. High fan-out means too many responsibilities and suggests
  decomposing; high fan-in means a bottleneck whose changes have wide blast radius.
- `GRAPH-SCATTERED-CONCEPT`: one concept cluster's members spread across many modules instead of
  living together.
- `GRAPH-NAMING-INCONSISTENCY`: two same-shaped functions in one cluster naming the same concept
  with synonymous verbs.
- Scope a scan to them by rule id, repeating `--rule` per id:

```bash
# pull just the graph findings out of a scan
auditr scan . --rule GRAPH-GOD-CONCEPT --rule GRAPH-SCATTERED-CONCEPT -f json
```

- Thresholds for all three live under `[tool.auditor.graph]`. See
  [configuration.md](configuration.md).

## Serving and exporting

- `graph serve` renders the UI and serves it on an ephemeral `127.0.0.1` port until Ctrl-C, opening
  a browser tab.
- It reuses the already-built graph when one exists, and only pays the scan plus build cost when the
  graph is missing or `--rebuild` is passed. `--no-open` skips the browser tab.
- On WSL the tab opens Windows-side (`wslview`, else `explorer.exe`, else `cmd.exe /c start`), which
  reaches `127.0.0.1` through WSL2 localhost forwarding.
- `graph export` writes Graphviz DOT to stdout. `--format svg` pipes that through the system `dot`
  binary; without graphviz installed it fails and tells you to use `--format dot`.
- `--cluster <id>` exports one cluster and `--symbol <name>` with `--depth` exports a symbol's
  ego-graph. With neither, it exports the whole graph.
