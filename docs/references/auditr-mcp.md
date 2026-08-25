# auditr-mcp reference

`auditr-mcp` runs the same audit engine as the CLI as a stdio
[MCP](https://modelcontextprotocol.io) server, so an agent calls the tools directly instead of
parsing CLI output. It takes no arguments; an MCP client launches it and speaks the protocol over
stdin/stdout. `auditor-mcp` is a kept alias and `python -m auditor.mcp_server` resolves to the same
entry point. It needs the `mcp` extra: `uv tool install "auditr[mcp]"`.

## Common invocations

```bash
# run the server over stdio (what a client launches)
auditr-mcp

# register it with Claude Code for this project only (--scope local is the default)
claude mcp add --scope local auditor -- auditr-mcp

# register it for every project
claude mcp add --scope user auditor -- auditr-mcp

# register it with Codex
codex mcp add auditor -- auditr-mcp

# run it from a checkout instead of an installed tool
claude mcp add auditor -- uv run --directory /path/to/auditor auditr-mcp
```

## Starting it

- Claude Code: `claude mcp add --scope project` writes a committable `.mcp.json` instead of the
  per-project local config.

```json
{
  "mcpServers": {
    "auditor": { "command": "auditr-mcp", "args": [] }
  }
}
```

- The auditor Claude Code plugin bundles its own server config, so enabling the plugin registers
  the server with no `claude mcp add` at all. See
  [claude-code-plugin.md](claude-code-plugin.md).
- Codex: `codex mcp add auditor -- auditr-mcp`, or an entry in `~/.codex/config.toml` (a
  project-scoped `.codex/config.toml` works in trusted projects).

```toml
[mcp_servers.auditor]
command = "auditr-mcp"
args = []
# env = { AUDITOR_HOME = "/home/you/.auditor" }   # pin the shared index location
```

- Docker, when there is no local Python or uv: the repo mounts at `/auditor` and the index
  persists in a named volume.

```bash
# build the image once
docker build -t auditor:latest .

# Claude Code, containerized
claude mcp add auditor -- docker run -i --rm \
  -v "$PWD:/auditor" -v auditor-index:/root/.auditor \
  --entrypoint auditr-mcp auditor:latest

# or through the compose service, which sets the same entrypoint and volumes
docker compose run --rm -T auditor-mcp
```

```toml
# Codex ~/.codex/config.toml, containerized
[mcp_servers.auditor]
command = "docker"
args = ["run", "-i", "--rm",
        "-v", "${PWD}:/auditor", "-v", "auditor-index:/root/.auditor",
        "--entrypoint", "auditr-mcp", "auditor:latest"]
```

- `AUDITOR_HOME` moves the shared index and cache directory. Set it in the client's `env` block
  when several clients must share, or avoid sharing, one index. Every environment variable is
  listed in [configuration.md](configuration.md).

## Tools

- Audit: `scan` (a file or directory), `report` (one file, stateless), `manifest` (a Python file's
  AST class and function manifest, no detectors), `discover` (auditable files with their classified
  role), `aggregate` (roll the incremental index into `AUDIT.md`), `finding_detail` (one finding's
  full record).
- Rules and suppressions: `rules_list` (the detector registry, filterable by category, standard or
  framework; `root` picks the repo whose trusted plugins load, and every row carries the `source`
  it was registered from), `ignore_add`, `ignore_list`, `ignore_remove`.
- Malware backends: `malware_status`, `malware_update_dbs`, `malware_install`. Only
  `malware_update_dbs` and `malware_install` touch the network, and only when called.
- Semantic graph, always registered: `graph_build`, `graph_search`, `graph_usages`,
  `graph_related`, `graph_neighbors`, `graph_flow`, `graph_concept`, `graph_clusters`,
  `graph_overview`, `graph_unresolved`. The graph libraries are core dependencies, so
  `auditr[mcp]` is enough. See [graph.md](graph.md).
- `graph_overview` caps `god_concepts` and `bottlenecks` at 5 entries each and reports the true
  totals as `god_concept_count` and `bottleneck_count`.
- `graph_flow(symbol, path, direction, depth, limit, kinds, include_tests, expand_hubs, stop_at)`
  returns a nested tree rather than a flat list: one call reads a whole code path.
  `direction="in"` reverses it. Reach for it instead of chaining `graph_neighbors`, and read the
  `modules` list first. `limit` counts emitted nodes and is clamped to 1..1000; the default of
  200 is about 50 KB of JSON.
- Its pruning knobs mirror the CLI: `stop_at` takes module globs and is the way to keep a wide
  tree readable, `kinds` follows extra edge kinds (validated, so an unknown value is an error),
  `include_tests` keeps test symbols, `expand_hubs` opens the nodes the hub rule collapsed.
- A node's `hub` is `{count, kind, collapsed}` or `null`; `collapsed` is true only where the hub
  rule refused to expand it, never at the start symbol, under `expand_hubs`, or on the last level
  `depth` reached.
- `graph_unresolved` returns the refinement queue: one row per fact the deterministic resolver
  could not place, worst first, filtered by `reason` and `call_form` (both repeatable lists,
  validated, so an unknown value is an error), `external` and `limit`. Read it alongside
  `graph_usages` before calling a symbol dead. Rows flagged `externally_bound` name a non-repo
  import and sort last; pass `external=false` to drop them. Like `graph_overview`, it caps its
  `definers` and `candidates` lists and reports the true totals as `definers_count` and
  `candidates_count`; `auditr graph unresolved --json` returns the same keys.
- Refinements: `graph_refine_begin`, `graph_refine_propose`, `graph_refine_commit`,
  `graph_refine_abort`, `graph_refine_status`, `graph_refinements`, `graph_log`. The flow is: read
  `graph_unresolved`, `graph_refine_begin` to open a run, one `graph_refine_propose` per correction,
  then `graph_refine_commit` (which rebuilds) or `graph_refine_abort`.
- A proposal is checked against the source file's own AST facts: the destination's short name has to
  appear in the caller's facts for that edge kind and call form, the file has to still hash to what
  the build cached, the name must not be imported from outside the repo, and the destination must
  define the name. A failed check comes back as `outcome: "rejected"` with a `verify` code, and the
  rejection is recorded. So is a payload that is not a legal proposal at all, including one naming
  an unknown `edge_kind` or `call_form`: the values that cannot be read are dropped and the row is
  stored with `refusal: "invalid"` and the complaint, never a validation traceback. Only an unknown
  `kind` is an error instead, because the kind chooses the shape.
- `graph_refinements` and `graph_log` answer the newest rows first, capped by `limit` (default 50,
  at most 500), with the total the same filters match as `refinement_count` / `run_count` and a
  `truncated` flag, so a full page is never read as the whole list. `filtered` says why a page is
  empty, and the default `graph_log` run view sets it because it hides assessment-only runs; the
  statuses it hid are in `hidden_statuses`.
- A run's `refinements` is `{committed, rejected}`, the same split `graph_refine_status` reports and
  the same one the run's `summary` line counts.
- Staged proposals live in the server process, so one run is opened, filled and committed through
  one server. `graph_refine_status` reports `staged_here: false` in any other process.
  `AUDITOR_REFINE_RUN` pre-binds every tool to one run, which is how a runner-spawned server works
  without passing `run_id` on each call.
- Until `auditr graph eval` has produced numbers for this repo, every `add_edge`, `retarget_edge`,
  `resolve_ambiguous` and `move_node` lands `pending` and needs
  `auditr graph refinements accept <id>` before a build applies it. `confirm_edge`,
  `relabel_cluster`, `annotate_node` and `unresolvable` go active immediately. There is deliberately
  no MCP tool for `accept`, `revert`, `pin` or `prune`: activating a correction is a human step.
- Every tool is annotated so clients can skip confirmation prompts and cache results: read-only for
  everything that only reads, mutating for `ignore_add`, `graph_build`, `malware_update_dbs`,
  `malware_install` and the four `graph_refine_*` tools that write (`begin`, `propose`, `commit`,
  `abort`), destructive for `ignore_remove`. Destructive means a row is deleted, which is why
  `graph_refine_abort` is only mutating: it drops staging that was never written.
  `graph_refine_begin` and `graph_refine_propose` are additionally marked non-idempotent, because
  each call opens a run or stages another proposal: a client must not silently retry them. No tool
  touches an open world; all of them work on the local repo.
- Every tool resolves its project root, loads the repo policy and opens the shared index through
  one seam, so a tool always addresses the same checkout identity the CLI does. A repo whose
  configuration does not load comes back as a one-line tool error naming the offending key, never a
  traceback.
- `graph_build` waits at most `graph.rebuild_lock_timeout_seconds` for another process's build
  before it comes back as a tool error naming the lock file, so a wedged build is never a hung tool
  call.
- `scan` takes the same scoping the CLI does, including `severity`, `rule`, `since` (audit only
  what changed against a git ref, with the whole repo still scanned so cross-file rules hold),
  `profile`, `isolated`, `malware`, `fail_on` and a `config` override dict.

## Output format

- `scan` and `report` default to `detail="compact"`: a top-level `rules` map emitted once
  (`rule_id` to category, verdict kind, checklist item, standard refs, suggestion), and slim
  per-finding objects of `{rule_id, severity, line, message}`. Per-finding `evidence` and repeated
  rule metadata are dropped.
- `detail="summary"` returns counts only (`totals`, `by_rule`, `by_file`). `detail="full"` returns
  every field on every finding, and because that payload is large it comes back as a
  `ResourceLink` to read on demand rather than inline.
- `limit` (compact only, default 50) caps the response to the worst findings and reports the
  surplus under `omitted`.
- `finding_detail(file, rule_id, line)` is the recovery path for one finding's `evidence`,
  `suggestion` and `standard_refs` after compact mode dropped them.
- `aggregate` also returns a `ResourceLink` rather than the markdown inline.
- A response-limiting middleware caps any single tool response at 500,000 bytes as a backstop.
  Resource reads, where the full artifacts live, are never truncated.
- A second middleware notes the config keys no model declares on stderr, once per repo the server
  is asked about. It never writes to stdout, where the protocol lives, and never fails a tool
  call. It reads the repo from the call's `path`, `file` or `root` argument, falling back to that
  argument's own default, so a call that leaves it out is still covered; a tool that declares none
  of them (`malware_status`, `malware_install`) is skipped.
- The CLI's own JSON (`auditr scan -f json`) is unaffected by any of this.

## Code mode

- Code mode is an experimental FastMCP transform: the client LLM writes a Python script that chains
  the tools in a sandbox and receives only the final value, so large intermediate payloads never
  enter its context.
- It is off unless both conditions hold: the `code-mode` extra is installed
  (`pip install "auditr[code-mode]"`, which pulls `fastmcp[code-mode]` and its sandbox) and
  `AUDITOR_CODE_MODE` is set in the server's environment.
- With the extra missing or the flag unset, `auditr-mcp` starts normally and serves the plain tool
  surface.
