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

# read the code path out of an entry point, four hops deep
auditr graph flow auditor/cli/scan.py::scan .

# who reaches a symbol, instead of what it reaches
auditr graph flow audit_target . --in

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
  `--rebuild` after upgrading auditor. It is refused with `--no-scan`, which would leave nothing to
  build from.
- `--rebuild` holds the rebuild lock across the clear, the rescan and the build, so no other build
  can see the half-rescanned graph.
- Routine staleness needs neither flag: the default auto-scan already picks up edited files.
- Setting `enabled = true` under `[tool.auditor.graph]` also makes a plain `auditr scan -i` populate
  graph facts. See [configuration.md](configuration.md).
- The build runs the `GRAPH-*` detectors, described below.
- The build reports `nodes`, `edges`, `clusters`, `unresolved`, `findings`, `refined` (the
  refinements it applied) and `expired` (the ones it wrote a new status for).
- A build takes a lock at `$AUDITOR_HOME/observer/locks/<key>.lock`, one per checkout, so two
  builds of the same repo never interleave and builds of different repos never wait on each other.
  If another process is mid-build, `graph build` prints `waiting for the observer's rebuild` and
  then proceeds.
- The lock is released when the holding process exits, so a crashed build never leaves one behind
  and no lock file ever needs deleting.

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
- `graph concept` prints the cluster's label, its member count and every member's symbol id. The
  count and the ids were both broken before 0.11: the renderer read keys the query never returned.
  The MCP `graph_concept` tool still caps its member list at `limit` and reports `member_count` and
  `shown` alongside.
- `clusters` lists every concept cluster with its id, label and member count.
- Worked recipes with real command output live in the plugin's
  [explore-graph recipes](../../plugin/skills/explore-graph/references/recipes.md).

## Flow

`graph flow` answers "what does this code path do" in one call, instead of a chain of `neighbors`
queries. It walks the graph breadth-first from one symbol and prints a tree.

- Outward it follows `calls` and `callback_arg`. `--in` reverses that: what reaches the symbol.
- The start symbol always expands, however wide it is. Hub collapsing applies to what it reaches.
- A reached method with overriders expands each overrider as `dispatches_to`, so a call to a base
  method shows the implementations it can land in. `--in` walks the other way, from an overrider
  to its base.
- A symbol registered in a registry module shows that module as a leaf. `--in` on the registry
  module expands every symbol registered there, which is how decorator-driven dispatch reads.
- The `modules` line above the tree is the ordered list of modules the path touches. That line,
  not the tree, is usually the architecture answer.
- Flags: `--depth` (default 4, 0 to 64), `--limit` (default 200 nodes, 1 to 1000, shallow levels
  finish first), `--kinds a,b` to follow extra edge kinds on top of the two defaults,
  `--include-tests` to keep test symbols, `--stop-at GLOB` (repeatable) to stop expanding inside a
  module, `--expand-hubs` to open a node the hub rule elided, `--json` for the raw payload.
- `--kinds` is validated against the edge kinds, so a typo is an error rather than a tree that
  silently omits the relation you asked for. `--stop-at` stays a free glob: a glob that matches
  nothing is a legitimate query.
- Markers in the tree:
  - `⊕ N elided` is a hub the walk refused to expand. A node is a hub when either count reaches
    `graph.flow_hub_fan_in` (default 40): the symbols that reach it, dispatch children included,
    or the children it would emit. `⊕ N hub` is the same fan on a node that expanded anyway: the
    start symbol, any node under `--expand-hubs`, and a hub on the last level `--depth` reached.
    Both counts are over production symbols only, whatever `--include-tests` says, so the mark
    describes the symbol and not the query: `--include-tests` can only widen the tree.
  - In the JSON payload that pair is one `hub` object, `{"count": N, "kind": "fan_in", "collapsed":
    true}`, or `null` on a node whose fan stayed under the floor.
  - `↺ seen` is a node already shown elsewhere in the tree, `↺ cycle` a node that is its own
    ancestor. Both are shown once and not expanded again.
  - `⊣ stop` is a node a `--stop-at` glob matched: the path reached it, the tree does not go in.
  - `? name` is a call the resolver could not place, dimmed when the name is bound from outside
    the repo.
- Bare names resolve the same way `usages` does: the highest-rank match becomes `resolved` and the
  rest are listed under `ambiguous`.
- `graph export --flow <symbol>` renders the same walk as Graphviz DOT, and takes the same knobs:
  `--in`, `--depth`, `--limit`, `--kinds`, `--include-tests`, `--expand-hubs` and `--stop-at`. All
  but `--depth` are errors without `--flow`, since the overview and ego modes do not walk;
  `--depth` also sets the ego export's hop count.

```bash
# stop at the database layer and keep the tree readable
auditr graph flow auditor/engine.py::audit_target . --stop-at 'auditor/database/*'

# follow inheritance too, and open the hubs
auditr graph flow auditor/models.py::Finding . --kinds inherits --expand-hubs
```

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

## Refinements

```bash
# every correction recorded for this checkout, oldest first
auditr graph refinements list
# only the ones a build is applying
auditr graph refinements list --status active --status pinned
# activate a pending correction; the next build applies it
auditr graph refinements accept 12
# take one back out; the row stays, with its reason
auditr graph refinements revert 12
# keep one through anchor drift and dead builds
auditr graph refinements pin 12
# finish runs a dead process left open, and drop assessment-only runs past the retention window
auditr graph refinements prune
```

- A correction is proposed through the `graph_refine_*` MCP tools, never by hand: every one is
  checked against the source file's own AST facts before it is stored. See
  [auditr-mcp](auditr-mcp.md).
- `status` is one of `pending`, `active`, `stale`, `redundant`, `reverted`, `pinned`, `superseded`,
  `rejected`. An unknown value is an error naming the set.
- Until `auditr graph eval` has produced numbers for this repo, every `add_edge`, `retarget_edge`,
  `resolve_ambiguous` and `move_node` lands `pending` and needs an explicit `accept`.
  `confirm_edge`, `relabel_cluster`, `annotate_node` and `unresolvable` go active immediately.
- `accept`, `revert` and `pin` are deliberately CLI-only. An agent may propose and commit; deciding
  that a pending correction is right is a human step, and no MCP tool can take it.
- They change a status and nothing else. The build is the only place a refinement reaches the graph,
  so run `auditr graph build` afterwards.
- `prune` does two things, and reports both. It finishes runs left `queued` by a process that died
  (`observer.limits.stranded_run_seconds`, default an hour) as `skipped`, because nothing else can
  reach them. It then deletes assessment-only runs (`skipped`) past `observer.skipped_retention_days`
  together with the `rejected` refinements they own, and never a run that owns a live refinement or
  a tuning row. Nothing live is ever deleted.

## Refinement overlay

- The graph the query commands read is the deterministic build plus an overlay of active
  refinements. `auditr graph refinements` lists and steers them and the `graph_refine_*` MCP tools
  are how an agent proposes one; this section describes what a build does with the rows once
  something puts them there, and what the three judgement layers decide before a row is written at
  all.
- A refinement is recorded against a repo *identity* (the git common dir), not a scan partition, so
  every worktree of one checkout shares them.
- Every edge carries a provenance: `deterministic` when the resolver produced it, `refined` when an
  active refinement did. The visualization payload and the flow tree carry it as a field, and both
  `graph export` DOT paths draw a `refined` edge dashed.
- The deterministic edge set is never rewritten. An overlay edge is an addition, and a
  `retarget_edge` is the only kind that moves one, by replacing it with a `refined` edge.
- The `GRAPH-*` detectors run on a graph no refinement touched: the edge list captured before the
  overlay, re-ranked and re-clustered over that list, and the nodes that second pass stamps rather
  than the overlaid ones. A refinement can never create or silence a finding, and can never move
  which symbol one is reported on. A build in which the overlay placed no edge and moved no node
  skips the second pass, because it would reproduce the merged one exactly.
- A refinement expires on its own:
  - `stale` when a node it is anchored to is gone or its structural facts changed, or when it had
    no effect for `refine_max_noop_builds` consecutive builds.
  - `redundant` when the resolver starts producing the same edge. That is the success case, and it
    is decided against the resolver's own edges only: an edge another refinement placed in the same
    build makes this one applied with nothing to add, so reverting the first cannot lose the second.
  - `pinned` refinements are never auto-staled by any path; a moved anchor marks them `drifted`
    instead, and the no-op counter still advances so a long-dead pin stays visible.
  - `drifted` is rewritten on every build, so a restored anchor clears it.
- A refinement whose ids belong to a different partition of the same checkout is skipped in
  silence: not applied there, and not staled there either.
- A build that holds no cached facts for a file the refinement names gives it no verdict at all:
  not applied, not staled, not counted. A rescan in flight is not a deleted symbol.
- Cluster refinements record the member set they were made against and re-attach to whichever
  cluster still overlaps it by at least `refine_cluster_jaccard`. Below that they go `stale`. A
  member another partition owns lowers the overlap rather than putting the whole refinement out of
  scope, and a cluster a `move_node` empties is dropped rather than shipped with no members.
- Queue rows retire two ways. A refinement the build applied removes exactly its own
  `(node_id, name)` row, as does an `unresolvable`, which answers by declaring the pair
  unanswerable; a refinement the build staled or scored a no-op leaves its row, so the fact stays
  briefable until something replaces it. The build-pass rows (`generic_label`,
  `singleton_cluster`, `text_sparse`) are rebuilt from the overlaid clustering instead, so a
  `relabel_cluster` or a `move_node` stops producing them without any retirement step.

## Refinement verdicts

- A proposal names one of five edge kinds (`calls`, `references_type`, `callback_arg`, `inherits`,
  `overrides`) and carries a reason. The fact check answers one of:
  - `ok`: the src node's own facts back an edge of this shape. With more than one definer that is
    not a claim that this destination is the right one.
  - `unverified`: a kind with no verifier (`confirm_edge`, `relabel_cluster`, `annotate_node`,
    `unresolvable`, `move_node`). Accepted, tiered on shape.
  - `no_such_path`, `not_loaded`, `stale_file`: the path is not a file here, the caller never
    handed the file in, or the file no longer hashes to what the build cached. Only the last one is
    fixed by rebuilding the graph.
  - `no_src_node`, `no_fact`: the src node is not in its file, or the fact tuple for that edge kind
    and call form does not name the destination. A bare name the src binds itself is no fact, which
    is the rule the unresolved queue applies to the same call.
  - `externally_bound`, `not_a_definer`, `bad_node_kind`: the caller's module imports the name from
    outside the repo, the destination does not define it (or is outside the gated candidates for
    `resolve_ambiguous`), or the endpoints are node kinds the resolver never pairs.
- The tier is the proposal's shape (spec 9.2). Tier A is the kinds that cannot add an edge plus a
  verified `resolve_ambiguous`; tier B is a verified `add_edge` on a bare or `self` call with one
  definer and no external binding; everything else is tier C.
- Whether a tier activates is measured, not assumed. Tier A's `resolve_ambiguous` waits for the
  decoy suite, tier B waits for the add suite's stratum matching the proposal (same module, direct
  import, or neither) plus a collision control with no false adds. A suite stratum that ran no
  trials proves nothing. With no eval rows every tier but the four safe kinds starts `pending`.
- At commit a proposal is checked against prior work:
  - `redundant`: the resolver now produces this edge. Terminal, never re-briefed.
  - `already_resolved`: the source already has a deterministic edge of the same kind for the same
    short name, pointing elsewhere. Only `add_edge` trips this, because `retarget_edge` names that
    edge on purpose.
  - `duplicate`: an active refinement already adds this edge, so the proposal is stored as a
    `confirm_edge`.
  - `contradicts`: an active refinement already points the same source at another destination for
    this name.

## Graph findings

- The three `GRAPH-*` detectors run during `graph build`. Their findings are `suggestion` severity
  with a `candidate` verdict, and they surface in the normal finding stream
  (`auditr scan`, `auditr aggregate`), not in a graph subcommand.
- `GRAPH-GOD-CONCEPT`: a concept hub. High fan-out means too many responsibilities and suggests
  decomposing; high fan-in means a bottleneck whose changes have wide blast radius.
- Which of the two fired is stored on the finding as `subkind`, `fan_out` or `bottleneck`. It is a
  field, not a second rule id, so a baseline entry and an `auditor: skip` directive keep resolving
  by `GRAPH-GOD-CONCEPT`. The MCP `graph_overview` tool splits its two hub lists on it.
- `subkind` is null on every other rule. It is stored in the index, read by `graph_overview`, and
  included in the full record `finding_detail` returns. It is not part of the `-f json` finding
  shape.
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
- `--flow <symbol>` exports the flow tree instead: `rankdir=LR` with one `rank=same` row per depth,
  so the picture reads left to right as the call path. `--in` reverses it and `--depth` sets the
  hops (4 by default in flow mode, 1 in `--symbol` ego mode).
- The DOT carries the same marks the tree shows: a hub is doubled and magenta, a `--stop-at` node
  is dashed, a cycle is orange, an already-shown node is dotted, and a node with unplaced calls
  gets a `? N` second line in its label.
- The modes pick different node sets, so combining them is an error rather than a silent
  preference: `--flow` with `--symbol` or `--cluster`, `--symbol` with `--cluster`, and any
  walk-only knob without `--flow` (see the flow section above for the list). A `--flow` symbol
  the graph does not hold is an error too, not an empty DOT.
- `--limit` caps the flow walk (default 200 nodes, 1 to 1000) and the DOT records the cap in a
  comment on the second line, with `truncated` when it was hit.
