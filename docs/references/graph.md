# graph reference

`auditr graph` builds and queries a semantic graph of a repo: nodes are modules, classes and
functions, edges are structural relations (calls, overrides, imports) and semantic ones (name and
usage similarity). `auditr graph <subcommand> --help` lists every flag.

- Nothing extra to install: `numpy`, `scikit-learn`, `snowballstemmer` and `networkx` are core
  dependencies.
- The sub-app is imported on the first `graph` subcommand, so the rest of the CLI never pays its
  ~0.65 s import. The first graph command in a process is that much slower; the ones after it
  are not.

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

# what the deterministic resolver could not place, worst first
auditr graph unresolved .

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
- The build reports five counts: `nodes`, `edges`, `clusters`, `unresolved` and `findings`.

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

## The unresolved queue

`graph unresolved` lists the facts the deterministic resolver could not place. Every build rebuilds
the whole queue, and `graph build` reports its size as `unresolved`.

```bash
# the whole queue, worst first (the default limit is in `graph unresolved --help`)
auditr graph unresolved .

# only the names with a real candidate set
auditr graph unresolved . --reason ambiguous_name

# only the safely answerable shapes: bare calls and self calls
auditr graph unresolved . --call-form bare --call-form self

# hide the rows bound to a non-repo import
auditr graph unresolved . --no-external

# raw rows for an agent
auditr graph unresolved . --json --limit 500
```

- Rows are ordered worst first: ambiguous names, then `self`/bare calls, then attribute calls, then
  the label and cluster reasons. Externally bound rows sink below equal-priority real ones.
- `--reason` and `--call-form` are repeatable and combine. Both are validated against their value
  lists, so a typo is an error, not an empty queue. `--limit` must be at least 1.
- An empty result names its cause: a filter that matched nothing says so, and only a queue that was
  never built points at `graph build`.
- Reasons:
  - `ambiguous_name`: two or more repo definitions are reachable from the call site, so the
    resolver refused to pick one.
  - `unimportable_name`: the repo defines the name, but the calling module cannot import it.
  - `text_sparse`: the symbol has fewer than four distinct concept tokens, so it gets no
    similarity edges.
  - `generic_label`: a cluster whose label fell back to `cluster-N` because no member contributed
    a token.
  - `singleton_cluster`: a cluster with one member.
- A resolver row only exists when the name has at least one repo definition that the caller's role
  can see. Test-only definitions are invisible to production callers, which is what keeps the queue
  small. The build-pass rows (`text_sparse`, `generic_label`, `singleton_cluster`) are about a
  symbol or a cluster rather than a name, so they carry no definers.
- Test code never queues anything, on either side. Only production and script callers produce
  resolver rows, and a test-role symbol never produces a `text_sparse` row.
- A row is dropped when the node already has an edge of that kind to a symbol of the same short
  name, so a call resolved through the typed-receiver path is never queued twice.
- `typed_call` rows survive only when the receiver's declared type is a repo class whose whole base
  chain resolves in-repo, so `str.lower`, `Path.mkdir` and pydantic receivers never appear. A
  receiver known not to be a repo class also removes the plain attribute row for that call: the
  call is settled, the same-named repo function is simply not what it calls.
- `call_form` is `self` only for a direct `self.method()` or `cls.method()`. A chained
  `self.dep.method()` is `attr` with a receiver root of `self`.
- A name called both bare and through a receiver in the same function gets one row, in the bare
  form, because that is the form a reader can settle from one file. When the function itself binds
  the bare name, the attribute form wins instead, so the real miss still surfaces.
- A bare row is never emitted for a name the function itself binds. That covers every parameter
  form (including keyword-only, `*args` and `**kwargs`), nested `def`/`class`/lambda names and
  their parameters, `except ... as` targets, function-local imports, and every name it assigns.
- A receiver whose declared type is settled outside the repo silences only the calls on that
  receiver. `p.run()` on a `p: Path` does not hide a `job.run()` in the same function.
- `ext-bound` (`externally_bound` in JSON) marks a row whose bare name or receiver root the calling
  module imports from outside the repo, such as `re.search` or `subprocess.run`, including through
  a module-level alias like `_RX = re.compile(...)`. Those rows are kept for display, sort last and
  are not worth chasing; `--no-external` drops them. A bare source that names a sibling module of
  the caller's own package (`from _common import x` inside `plugin/hooks/`) is a repo import, not
  an external one.
- `definers` and `candidates` render as counts in the table. In `--json` and through the MCP tool
  they are node-id lists capped at the same limit, with the true totals in `definers_count` and
  `candidates_count`, so both surfaces carry the same keys.
- The queue is empty until `graph build` has run. This release bumps the index schema, so the
  cached facts are dropped on first use and the next `graph build` re-extracts every file. No
  `--rebuild` is needed.

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
