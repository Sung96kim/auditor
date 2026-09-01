# graph reference

`auditr graph` builds and queries a semantic graph of a repo: nodes are modules, classes and
functions, edges are structural relations (calls, overrides, imports) and semantic ones (name and
usage similarity). `auditr graph <subcommand> --help` lists every flag. Nothing extra to install
for the graph itself; `refine` and `eval` drive a model and need the `observer-claude` extra.

## Common invocations

```bash
# build the graph; auto-scans first to extract facts
auditr graph build .

# who uses a symbol and what it depends on, with full counts
auditr graph usages ComponentBlueprint .

# read the code path out of an entry point, four hops deep
auditr graph flow auditor/cli/scan.py::scan .

# find the exact symbol id by name, or ask in plain English when you do not know the name
auditr graph search Blueprint .
auditr graph search "how much money is left to spend today" .
auditr graph neighbors get_user . --depth 2
auditr graph related get_user .

# the concept cluster matching a term, and every cluster with its size
auditr graph concept tenant .
auditr graph clusters .

# what the deterministic resolver could not place, worst first
auditr graph unresolved .

# let a model work the queue under a directory, then see what changed
auditr graph refine auditor/cli
auditr graph log .

# interactive UI on a local port, and Graphviz DOT on stdout
auditr graph serve .
auditr graph export . --format dot > graph.dot
```

- `TARGET` defaults to `.` on every subcommand and resolves to the repo root.
- Every query subcommand takes `--json` for the raw payload. `serve` and `export` do not.
- The sub-app is imported on the first `graph` subcommand, so the rest of the CLI never pays its
  numpy and scikit-learn import cost.

## Building

- `graph build` runs an incremental scan with graph fact extraction forced on, then builds nodes,
  edges and clusters. Nothing has to be enabled in config first.
- `--no-scan` builds from cached facts only. Use it when a scan just ran and nothing changed since.
- `--rebuild` discards the cached graph facts and re-extracts from scratch. Facts are keyed by file
  content, so an extractor change does not invalidate facts already cached under the same hash; run
  `--rebuild` after upgrading auditor. It is refused with `--no-scan`, which would leave nothing to
  build from. One lock hold covers the clear, the rescan and the build, so no other build can see
  the half-rescanned graph.
- Routine staleness needs neither flag: the default auto-scan already picks up edited files.
- Setting `enabled = true` under `[tool.auditor.graph]` also makes a plain `auditr scan -i` populate
  graph facts. See [configuration.md](configuration.md).
- The build reports `nodes`, `edges`, `clusters`, `unresolved`, `findings`, `refined` (the
  refinements it applied) and `expired` (the ones it wrote a new status for).
- A build takes a lock under `$AUDITOR_HOME/observer/locks/`, one per checkout, so two builds of
  the same repo never interleave and builds of different repos never wait on each other. While
  another process holds it, `graph build` prints that it is waiting and then proceeds. The lock is
  released when the holding process exits, so no lock file ever needs deleting.

## Querying

- `usages` groups a symbol's edges by kind into `used_by` (incoming) and `depends_on` (outgoing).
  Each group carries a full `count` and a rank-ordered `sample`; `total_in` and `total_out` are the
  true totals. `--sample` sets the sample size (default 5).
- Reach for `usages` as the find-references and blast-radius query. `neighbors` truncates without
  reporting totals.
- An empty `used_by` with `total_in` of 0 makes a symbol a dead-code candidate, not a confirmed one.
  The graph is static and repo-partitioned, so it misses dynamic dispatch and out-of-repo callers;
  grep the name as a string literal before deleting anything.
- Bare names resolve fuzzily: the highest-rank match becomes `resolved`, the rest are listed under
  `ambiguous`. Run `search` first when a short name could match several nodes.
- `neighbors` walks structural edges (`calls`, `overrides`, `inherits`, `references_type`,
  `callback_arg`, `registered_in`, `contains`, `imports`) out to `--depth` hops (default 1),
  tagging each hit with its edge kind, direction and hop count.
- `related` walks semantic edges (`name_similar`, `usage_similar`) instead, so it answers "what is
  conceptually near this", not "what calls this". `--limit` defaults to 10.
- `search` answers by name, and by meaning only when no name answers. Node ids containing the term
  come first, highest rank first, and when there are any they are the whole page, so an exact-name
  lookup returns what it always returned. A term no id contains falls through to the symbols whose
  naming document ranks nearest to it in the graph build's tf-idf + LSI space, each carrying that
  cosine as `score`. A ranked row always scores above the 0.05 relevance floor, so `score: 0.0`
  still means the row came from the name half. Three cases, kept apart:
  - a name match suppresses ranking entirely, and `search Blueprint` returns its two rows;
  - a word the build never fitted returns nothing at all: measured on this repo, `kubernetes` and
    `webhook` are in no docstring and no id, and both answer with `[]`;
  - a word the corpus does hold but no id carries is ranked, and the page is not empty. Measured:
    `search serializer` returns a full 20 rows topping out at cosine 0.50, and this repo has no
    serializer. The floor drops noise, not a weak topic, so an in-vocabulary miss looks like a
    result.

  Read the ranked half as a shortlist to skim, not as a lookup: on this repo's own graph the
  labelled answer to a hand-written question is on a 20-row page for 13 of the 40 questions the
  retrieval gate asks. Symbols the build marked text-sparse, and module nodes, carry no naming
  document and never appear in the ranked half; find those by name. `--limit` is 1 to 1000 and
  defaults to 20. An index built before this ranking existed holds no fit, and `search` is the
  substring scan alone until the next `graph build`.
- `concept` returns the whole membership of the cluster a term belongs to, matching the cluster
  label first and member names second, and prints the label, the member count and every member id.
  The MCP `graph_concept` tool caps its member list and reports `member_count` and `shown`.
- `clusters` lists every concept cluster with its id, label and member count.
- Worked recipes with real command output live in the plugin's
  [explore-graph recipes](../../plugin/skills/explore-graph/references/recipes.md).

## Flow

`graph flow` answers "what does this code path do" in one call, instead of a chain of `neighbors`
queries. It walks the graph breadth-first from one symbol and prints a tree.

```bash
# stop at the database layer and keep the tree readable
auditr graph flow auditor/engine.py::audit_target . --stop-at 'auditor/database/*'

# follow inheritance too, and open the hubs
auditr graph flow auditor/models.py::Finding . --kinds inherits --expand-hubs
```

- Outward it follows `calls` and `callback_arg`. `--in` reverses that: what reaches the symbol.
- The start symbol always expands, however wide it is; a bare name resolves there the same way
  `usages` resolves one. Hub collapsing applies to what it reaches.
- A reached method with overriders expands each overrider as `dispatches_to`, so a call to a base
  method shows the implementations it can land in. `--in` walks the other way.
- A symbol registered in a registry module shows that module as a leaf. `--in` on the registry
  module expands every symbol registered there, which is how decorator-driven dispatch reads.
- The `modules` line above the tree is the ordered list of modules the path touches. That line,
  not the tree, is usually the architecture answer.
- Flags: `--depth` (default 4, 0 to 64), `--limit` (default 200 nodes, 1 to 1000, shallow levels
  finish first), `--kinds a,b` to follow extra edge kinds, `--include-tests` to keep test symbols,
  `--stop-at GLOB` (repeatable) to stop expanding inside a module, `--expand-hubs` to open a node
  the hub rule elided.
- `--kinds` is validated against the edge kinds, so a typo is an error rather than a tree that
  silently omits the relation you asked for. `--stop-at` stays a free glob: a glob that matches
  nothing is a legitimate query.
- Markers in the tree:
  - `⊕ N elided` is a hub the walk refused to expand. A node is a hub when either count reaches
    `graph.flow_hub_fan_in`: the symbols that reach it, dispatch children included, or the children
    it would emit. `⊕ N hub` is the same fan on a node that expanded anyway. Both counts are over
    production symbols only, whatever `--include-tests` says.
  - In the JSON payload that pair is one `hub` object, `{"count": N, "kind": "fan_in", "collapsed":
    true}`, or `null` on a node whose fan stayed under the floor.
  - `↺ seen` is a node already shown elsewhere in the tree, `↺ cycle` a node that is its own
    ancestor. Both are shown once and not expanded again.
  - `⊣ stop` is a node a `--stop-at` glob matched: the path reached it, the tree does not go in.
  - `? name` is a call the resolver could not place, dimmed when the name is bound from outside
    the repo.
- `graph export --flow <symbol>` renders the same walk as Graphviz DOT and takes the same knobs.

## The unresolved queue

`graph unresolved` lists the facts the deterministic resolver could not place. Every build rebuilds
the whole queue, and `graph build` reports its size as `unresolved`.

```bash
# the whole queue, worst first
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
  lists, so a typo is an error, not an empty queue. `--limit` defaults to 50 and must be at least 1.
- An empty result names its cause: a filter that matched nothing says so, and only a queue that was
  never built points at `graph build`.
- Reasons:
  - `ambiguous_name`: two or more repo definitions are reachable from the call site.
  - `unimportable_name`: the repo defines the name, but the calling module cannot import it.
  - `text_sparse`: the symbol has fewer than four distinct concept tokens, so it gets no
    similarity edges.
  - `generic_label`: a cluster whose label fell back to `cluster-N`.
  - `singleton_cluster`: a cluster with one member.
- What keeps the queue small: a resolver row only exists when the name has at least one repo
  definition the caller's role can see, test code never queues anything on either side, a row is
  dropped when the node already has an edge of that kind to the same short name, and a name the
  function itself binds (any parameter form, a nested `def`/`class`/lambda, an `except ... as`
  target, a local import, an assignment) never produces a bare row.
- A `typed_call` row survives only when the receiver's declared type is a repo class whose whole
  base chain resolves in-repo, so `str.lower`, `Path.mkdir` and pydantic receivers never appear. A
  receiver known not to be a repo class also removes the plain attribute row for that call: the
  call is settled, and the same-named repo function is not what it calls.
- `call_form` is `self` only for a direct `self.method()` or `cls.method()`. A chained
  `self.dep.method()` is `attr` with a receiver root of `self`. A name called both bare and through
  a receiver in the same function gets one row, in whichever form a reader can settle from one file.
- `ext-bound` (`externally_bound` in JSON) marks a row whose bare name or receiver root the calling
  module imports from outside the repo, such as `re.search` or `subprocess.run`. Those rows sort
  last and are not worth chasing; `--no-external` drops them.
- `definers` and `candidates` render as counts in the table. In `--json` and through the MCP tool
  they are node-id lists capped at the same limit, with the true totals in `definers_count` and
  `candidates_count`.

## Refine

```bash
# let a model work the unresolved queue under a path
auditr graph refine auditor/cli
# the whole repo, on a named model
auditr graph refine "" --model sonnet
# what a run would be asked, with no run opened
auditr graph refine auditor/cli --brief
```

- One run is: open a row, render the brief for the queue rows under the scope, hand it to a model
  that reads the checkout with `Read`/`Grep`/`Glob` and proposes through an in-process `propose`
  tool, then commit under the rebuild lock. Every proposal goes through the same verifier an
  agent's does, so a runner cannot write a correction the tools would refuse.
- `SCOPE` comes before `TARGET`, and it is the one graph command whose first positional is
  optional, so a lone path binds to `SCOPE`. Point at another checkout with
  `auditr graph refine . /other/repo`. The scope is a repo-relative path prefix; `.`, `./` and `""`
  all mean the whole repo. A run may only propose corrections whose every endpoint is under its
  scope, so a cross-directory edge needs a wider run.
- `--runner` takes `auto`, `claude` or `codex`. `auto` takes the Claude runner when
  `claude-agent-sdk` is installed and this machine looks logged in, then the Codex runner when
  `openai-codex` is installed and `$CODEX_HOME/auth.json` exists, and otherwise refuses with the
  reason. A fallback from Claude to Codex says so, because the cost model changes with it: Claude
  reports what a run cost and Codex reports only tokens, so a Codex run's `cost_usd` is derived
  from a price table and stamped estimated. `--model` takes `haiku` or `sonnet` and applies to the
  Claude runner alone; the Codex model is `observer.runner.codex_model`. `--runner codex` together
  with `--model` exits 2 rather than dropping the flag. The check is on the flags: reaching the
  Codex runner through `observer.runner.agent` or through `auto`'s fallback still ignores
  `--model`, and the run row records the Codex model either way. An unknown value for either
  exits 2 naming the valid set.
- Four refusals, all exit 1 with one line: the runner asked for is not installed
  (`pip install 'auditr[observer-claude]'` or `'auditr[observer-codex]'`), neither is
  (`'auditr[observer]'`), or the runner that is installed has no credentials, in which case the
  line names the command to run to log in.
- The SDK ships its own `claude` binary. The runner uses the `claude` on your PATH when there is
  one and falls back to that bundle otherwise.
- The run is bounded by `observer.limits.max_nodes_per_run` (queue rows in the brief),
  `max_changes_per_run` (corrections staged), `max_turns` (the conversation) and
  `observer.budget.max_budget_usd_per_run`. All four are in [configuration.md](configuration.md).
- A target costs about two turns and the structured answer costs one more, so a run that works
  every target it is given wants `max_turns >= 2 * max_nodes_per_run + 1`. Nothing enforces it and
  the shipped defaults are deliberately below it: a run that runs out of turns is `aborted` with
  its cost kept, not an error. The budget is an advisory post-turn stop, not a hard cap, so
  `max_turns` is the only hard bound on the conversation.
- An aborted run keeps its cost but loses its staging. Only the proposals the verifier refused
  survive, because those are written the moment they are made.
- The run row records the brief it was first handed, the sha of the rules it was written under, the
  tool trace, the usage, the SDK session id, the model, the runner, the branch and the commit.
  `input_tokens` counts cached tokens too, and `cost_estimated` travels with `cost_usd`: a run that
  stopped before its result reports `$0.0000` with `cost_estimated` true.
- Exit codes: 0 when the run succeeded, 1 when no runner could run or the run did not succeed (the
  payload is still printed), 2 for a bad `--runner` or `--model` value.
- `--brief` renders the brief for a scope and stops. It opens no run and records nothing, so it is
  the way to see what a run would be asked before spending anything.

## Refinements

```bash
# every correction recorded for this checkout, newest first
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

- A correction is proposed by `graph refine` or through the `graph_refine_*` MCP tools, never by
  hand: every one is checked against the source file's own AST facts before it is stored. See
  [auditr-mcp](auditr-mcp.md).
- `status` is one of `pending`, `active`, `stale`, `redundant`, `reverted`, `pinned`, `superseded`,
  `rejected`. An unknown value is an error naming the set.
- The page is the newest rows, capped by `--limit` (default 50, at most 500). `--json` carries
  `refinement_count`, the number matching the same filters, and `truncated`; the table says
  "showing N of T" when there is more.
- `accept`, `revert` and `pin` are deliberately CLI-only. An agent may propose and commit; deciding
  that a pending correction is right is a human step, and no MCP tool can take it. They change a
  status and nothing else, so run `auditr graph build` afterwards.
- `prune` does two things, and reports both. It finishes the runs a dead process left open as
  `skipped`, because nothing else can reach them: a `queued` row past
  `observer.limits.stranded_run_seconds`, a `running` row past twice that, since a `running` row is
  open for a whole model call. It then deletes assessment-only runs past
  `observer.skipped_retention_days` together with the `rejected` refinements they own, and never a
  run that owns a live refinement or a tuning row.

## What a correction has to clear

- The fact check answers one of:
  - `ok`: the src node's own facts back an edge of this shape. With more than one definer that is
    not a claim that this destination is the right one.
  - `unverified`: a kind with no verifier (`confirm_edge`, `relabel_cluster`, `annotate_node`,
    `unresolvable`, `move_node`). Accepted, tiered on shape.
  - `no_such_path`, `not_loaded`, `stale_file`: the path is not a file here, the caller never
    handed the file in, or the file no longer hashes to what the build cached. Only the last is
    fixed by rebuilding the graph.
  - `no_src_node`, `no_fact`: the src node is not in its file, or the fact tuple for that edge kind
    and call form does not name the destination.
  - `externally_bound`, `not_a_definer`, `bad_node_kind`: the caller's module imports the name from
    outside the repo, the destination does not define it, or the endpoints are node kinds the
    resolver never pairs.
- The tier is the proposal's shape. Tier A is the kinds that cannot add an edge plus a verified
  `resolve_ambiguous`; tier B is a verified `add_edge` on a bare or `self` call with one definer
  and no external binding; everything else is tier C.
- Whether a tier activates is measured by `graph eval` below, not assumed. With no eval rows,
  `confirm_edge`, `relabel_cluster`, `annotate_node` and `unresolvable` go active immediately and
  every other kind lands `pending` awaiting `auditr graph refinements accept <id>`. A later failing
  eval takes an activation back.
- At commit a proposal is checked against prior work: `redundant` (the resolver now produces this
  edge; terminal), `already_resolved` (the source already has a deterministic edge of the same kind
  for the same short name, which only `add_edge` trips), `duplicate` (an active refinement already
  adds this edge, so it is stored as a `confirm_edge`) and `contradicts` (an active refinement
  points the same source at another destination for this name).

## Eval

```bash
# measure this runner before it may activate corrections
auditr graph eval --suite all
# one suite, a small draw, repeatable
auditr graph eval --suite add --sample 20 --seed 7
# a named model, machine readable
auditr graph eval --model sonnet --json
# the plan and its ceilings, without opening a run
auditr graph eval --dry-run
```

- An eval masks known-true edges of this repo's own deterministic graph, presents them to a runner
  as unresolved rows, and judges every proposal against the ground truth.
- `--runner` and `--model` mean what they do for `refine`, refusal included: `--runner codex
  --model <tier>` exits 2, because a Codex eval is filed under `observer.runner.codex_model`.
- Four suites ship. `add` masks a resolved `calls` edge of tier B's own shape. `decoy` presents the
  same truths as `ambiguous_name` rows offering the true destination among up to three wrong ones.
  `collision` presents the queue's externally bound rows, where the only right answer is
  `unresolvable` or silence. `negative` presents names this repo defines nowhere. `--suite
  fixtures` is refused, naming what it still needs.
- `add` is stratified by how far the destination is from the source: `same-module`,
  `direct-import`, `neither`. On this repo those hold 883 / 1,321 / 38 tier-B-shaped truths, so
  `neither` has too few to prove at the default bar and proposals of that shape keep landing
  `pending`. `Stratum`'s docstring carries the same three counts and a test pins the pair.
- `--sample` is per stratum (default 80, at most 500). A stratum draws `min(sample, available)`,
  and the report names `short`, `empty`, `stopped`, `off_target`, `unprovable_drawn` and
  `unprovable_judged` strata.
- `add` and `decoy` clear on the Wilson 95 per cent lower bound of their precision reaching
  `observer.tuning.min_precision`; `collision` and `negative` clear on having produced no false add
  over at least one trial. The bound reads `correct + wrong`, not `n`, so a stratum a runner mostly
  ignored is unprovable however large its draw was. The smallest flawless run that clears the bar
  is in [configuration.md](configuration.md); a stratum with fewer truths than that on a repo
  cannot be proven there. An off-target proposal is scored against the stratum it was proposed
  under, so a suite cannot clear its gate over proposals nobody asked for.
- A stratum whose planned runs did not all complete writes no row, and a run whose brief did not
  carry every trial of its batch is `unbriefed` and measures nothing. An abort is not a
  measurement, so the last complete measurement stands.
- The rows live in `graph_evals`, one per `(runner, model, suite, stratum)`, with the controls
  under one stratum, `all`. The latest row per key governs. Eval runs appear in `auditr graph log`
  with `trigger_kind` `eval` and never in `auditr graph refinements`: the proposals go to a judge,
  not to the ledger.
- Cost: one run per `observer.limits.max_nodes_per_run` trials, each bounded by
  `observer.budget.max_budget_usd_per_run`, and the whole invocation by
  `observer.budget.max_budget_usd_per_eval`. The eval stops before opening a run that would cross
  the eval ceiling, and the report says `stopped: budget`. On the human path the plan prints on
  stderr before the first run opens; under `--json` nothing precedes the document, so
  `--dry-run --json` reads the plan without spending.
- `--json` carries one document: `plan` (`sample`, `seed`, `suites`, `strata`, `runs_planned`, the
  two ceilings), `suites` (one tally per measured stratum, each with a nested `spend` and an
  `off_target` count), `notes` (the six lists above), `activation` (`proven`, `tier_b`,
  `resolve_ambiguous`), `cost_usd` and `runs`.
- Tier B needs its own add stratum and the `collision` control, so `--suite add` alone proves
  strata and activates nothing.
- Exit codes: 0 when every planned run closed or `--dry-run` was given, 1 when no runner could run
  or a run did not close (the partial payload is still printed), 2 for a bad option value.

## Provenance log

```bash
# who changed the graph, newest first
auditr graph log
# the corrections instead of the decisions that made them
auditr graph log --refinements
# only the runs that failed, in the last two hours
auditr graph log --status failed --since 2h
# include the runs that never reached a runner
auditr graph log --skipped
```

- `--runs` (the default) shows one row per decision, with `n`, the number of refinement rows that
  run owns; `--refinements` shows the corrections themselves. Both are newest first, which is the
  opposite of the order a build applies them in. A run is `queued` only until its brief is
  recorded: every runner stamps `running` before the first model turn, so a run with turns burning
  reads `running`.
- The `summary` column is the model's own one-line answer when it gave one. Without one it splits
  that count for a finished run ("1 committed, 0 rejected"), because a run is not credited with the
  proposals it refused. An aborted run shows its reason there instead.
- `--status` is validated against whichever view is showing, so a run status in the refinements
  view is an error naming the valid set, not an empty page.
- `--since` takes a duration (`90s`, `45m`, `2h`, `7d`) or an ISO date (`2026-08-20`,
  `2026-08-20T14:00:00`). It is not a git ref: `scan --since` scopes files, a log is scoped by time.
- `--skipped` adds the runs that never reached a runner, hidden by default. It is a runs-view
  filter; pairing it with `--refinements` is an error naming the filters that view does take.
  Three kinds of row show up under it, told apart by whether they carry an assessment: runs evicted
  from the open-run registry and stranded runs the sweep closed both put their reason in `error`,
  while an assessment row carries `trigger_detail.assessment` and puts its reason there.
- An assessment row carries a second line: `looked at <paths>: <reason>, <status>`. It names at
  most 3 paths and counts the rest, and the status is the row's own rather than a stored word, so a
  row whose status later changed still reads true.
- An empty page names the cause it can prove, in this order: the filter you set, the rows the view
  hid on its own (with the count), then nothing recorded at all. `--json` carries the same three as
  `narrowed_by`, `hidden_statuses` and `hidden_count`.
- The `when` column is local time as `MM-DD HH:MM` and carries no year; `--json` carries the epoch.
- The page is capped by `--limit` (default 50, at most 500). `--json` carries `run_count` and
  `refinement_count`, the number matching the same filters, and `truncated`.
- Under `--json` a model-driven run adds `system_prompt_sha`, `prompt_chars` and `tool_calls`, and
  every run row carries `trigger_detail` with five keys: `files` and `targets`, the paths the
  trigger named and the node pairs the run was asked about, each capped at 10; `file_count` and
  `target_count`, the full sizes behind those caps; and `assessment` when there is one. The
  assessment travels as counts, never as node ids, so a fifty row page cannot carry thousands of
  them. One of those counts, `affected_flow`, is `0` on every run the observer opens: nothing
  records a flow query yet, so the loop hands the assessment an empty set and only an eval
  fixture fills it.

## Refinement overlay

- The graph the query commands read is the deterministic build plus an overlay of active
  refinements, recorded against a repo identity (the git common dir), so every worktree of one
  checkout shares them.
- Every edge carries a provenance: `deterministic` when the resolver produced it, `refined` when an
  active refinement did. The flow tree and the visualization payload carry it as a field, and both
  `graph export` DOT paths draw a `refined` edge dashed.
- The deterministic edge set is never rewritten. An overlay edge is an addition, and a
  `retarget_edge` is the only kind that moves one. The `GRAPH-*` detectors run on a graph no
  refinement touched, so a refinement can never create or silence a finding.
- A refinement expires on its own. It goes `stale` when a node it is anchored to is gone or its
  structural facts changed, or when it had no effect for `graph.refine_max_noop_builds` consecutive
  builds; `redundant` when the resolver starts producing the same edge, which is the success case.
  A `pinned` refinement is never auto-staled: a moved anchor marks it `drifted` instead, rewritten
  on every build so a restored anchor clears it.
- Cluster refinements record the member set they were made against and re-attach to whichever
  cluster still overlaps it by at least `graph.refine_cluster_jaccard`. Below that they go `stale`.
- A build holding no cached facts for a file the refinement names gives it no verdict at all: a
  rescan in flight is not a deleted symbol. A refinement whose ids belong to another partition of
  the same checkout is skipped in silence.
- A refinement the build applied retires exactly its own `(node_id, name)` queue row, as does an
  `unresolvable`; one the build staled or scored a no-op leaves its row, so the fact stays
  briefable.

## Graph findings

- The three `GRAPH-*` detectors run during `graph build`. Their findings are `suggestion` severity
  with a `candidate` verdict, and they surface in the normal finding stream (`auditr scan`,
  `auditr aggregate`), not in a graph subcommand.
- `GRAPH-GOD-CONCEPT`: a concept hub. High fan-out means too many responsibilities; high fan-in
  means a bottleneck whose changes have wide blast radius. Which of the two fired is stored on the
  finding as `subkind`, `fan_out` or `bottleneck`. It is a field, not a second rule id, so a
  baseline entry and an `auditor: skip` directive keep resolving by `GRAPH-GOD-CONCEPT`.
- `subkind` is null on every other rule. It is stored in the index, read by the MCP
  `graph_overview` tool, and included in the record `finding_detail` returns. It is not part of the
  `-f json` finding shape.
- `GRAPH-SCATTERED-CONCEPT`: one concept cluster's members spread across many modules instead of
  living together.
- `GRAPH-NAMING-INCONSISTENCY`: two same-shaped functions in one cluster naming the same concept
  with synonymous verbs.
- Thresholds for all three live under `[tool.auditor.graph]`. See
  [configuration.md](configuration.md). Scope a scan to them by rule id, repeating `--rule`:

```bash
auditr scan . --rule GRAPH-GOD-CONCEPT --rule GRAPH-SCATTERED-CONCEPT -f json
```

## Serving and exporting

- `graph serve` renders the UI and serves it on an ephemeral `127.0.0.1` port until Ctrl-C, opening
  a browser tab. It reuses the already-built graph when one exists and only pays the scan plus
  build cost when the graph is missing or `--rebuild` is passed. `--no-open` skips the tab.
- On WSL the tab opens Windows-side (`wslview`, else `explorer.exe`, else `cmd.exe /c start`),
  which reaches `127.0.0.1` through WSL2 localhost forwarding.
- `graph export` writes Graphviz DOT to stdout. `--format svg` pipes that through the system `dot`
  binary; without graphviz installed it fails and tells you to use `--format dot`.
- `--cluster <id>` exports one cluster, `--symbol <name>` with `--depth` exports a symbol's
  ego-graph, `--flow <symbol>` exports the flow tree (`rankdir=LR`, one `rank=same` row per depth).
  With none of them it exports the whole graph. `--depth` defaults to 4 in flow mode and 1 in ego
  mode.
- The DOT carries the same marks the tree shows: a hub is doubled and magenta, a `--stop-at` node
  is dashed, a cycle is orange, an already-shown node is dotted, and a node with unplaced calls
  gets a `? N` second line in its label.
- The modes pick different node sets, so combining them is an error rather than a silent
  preference: `--flow` with `--symbol` or `--cluster`, `--symbol` with `--cluster`, and any
  walk-only knob without `--flow`. A `--flow` symbol the graph does not hold is an error too.
- `--limit` caps the flow walk (default 200 nodes, 1 to 1000) and the DOT records the cap in a
  comment on the second line, with `truncated` when it was hit.
