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

- <img src="../../assets/claude-color.svg" height="16" alt="Claude"> Claude Code:
  `claude mcp add --scope project` writes a committable `.mcp.json` instead of the per-project
  local config.

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
- <img src="../../assets/codex-color.svg" height="16" alt="Codex"> Codex:
  `codex mcp add auditor -- auditr-mcp`, or an entry in `~/.codex/config.toml` (a project-scoped
  `.codex/config.toml` works in trusted projects).
  The Codex plugin ships the same server config, so installing `codex-plugin/` registers it with
  no `codex mcp add` at all. See [codex-plugin.md](codex-plugin.md).

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
  AST manifest, no detectors), `discover` (auditable files with their classified role), `aggregate`
  (roll the incremental index into `AUDIT.md`), `finding_detail` (one finding's full record).
- Rules and suppressions: `rules_list` (filter by `category`, `standard` or `framework`; `root`
  picks the repo whose trusted plugins load, and every row carries the `source` it was registered
  from, as in [rules.md](rules.md)), `ignore_add`, `ignore_list`, `ignore_remove`.
- Malware backends: `malware_status`, `malware_update_dbs`, `malware_install`. Only the last two
  touch the network, and only when called.
- Semantic graph, always registered because the graph libraries are core dependencies:
  `graph_build`, `graph_search`, `graph_usages`, `graph_related`, `graph_neighbors`, `graph_flow`,
  `graph_concept`, `graph_clusters`, `graph_overview`, `graph_unresolved`. See [graph.md](graph.md).
- Refinements: `graph_refine_begin`, `graph_refine_propose`, `graph_refine_commit`,
  `graph_refine_abort`, `graph_refine_status`, `graph_refine_brief`, `graph_refine`,
  `graph_refinements`, `graph_log`.
- `scan` takes the same scoping the CLI does, including `severity`, `rule`, `since`, `profile`,
  `isolated`, `malware`, `fail_on` and a `config` override dict. See [scan.md](scan.md).

### Reading the graph

- `graph_flow(symbol, path, direction, depth, limit, kinds, include_tests, expand_hubs, stop_at)`
  returns a nested tree rather than a flat list, so one call reads a whole code path.
  `direction="in"` reverses it. Reach for it instead of chaining `graph_neighbors`, and read the
  `modules` list first. `limit` counts emitted nodes and is clamped to 1..1000; the default of 200
  is about 50 KB of JSON. Its pruning knobs mirror the CLI's flow flags.
- A node's `hub` is `{count, kind, collapsed}` or `null`; `collapsed` is true only where the hub
  rule refused to expand it, never at the start symbol, under `expand_hubs`, or on the last level
  `depth` reached.
- `graph_overview` caps `god_concepts` and `bottlenecks` at 5 entries each and reports the true
  totals as `god_concept_count` and `bottleneck_count`.
- `graph_unresolved` returns the refinement queue, worst first, filtered by `reason` and
  `call_form` (both repeatable lists, validated) plus `external` and `limit`. Read it alongside
  `graph_usages` before calling a symbol dead. Like `graph_overview` it caps its `definers` and
  `candidates` lists and reports the true totals; `auditr graph unresolved --json` returns the same
  keys.

### Proposing a correction

- The flow is: read `graph_unresolved`, `graph_refine_begin` to open a run, one
  `graph_refine_propose` per correction, then `graph_refine_commit` (which rebuilds) or
  `graph_refine_abort`. `graph_refine_brief` renders what a model-driven run would be asked for the
  same queue rows, and records that prompt on the run the first time it is read.
- `graph_refine` runs a model over the queue itself, in this server's own process and under the
  same limits as `auditr graph refine`. It needs the `observer-claude` extra and Claude
  credentials; without either it comes back as a one-line error naming the fix. Do not call it from
  inside a refinement run: the bound `propose` tool is that surface.
- Every proposal is checked against the source file's own AST facts. A failed check comes back as
  `outcome: "rejected"` with a `verify` code, and the rejection is recorded. So is a payload that is
  not a legal proposal at all: the values that cannot be read are dropped and the row is stored with
  `refusal: "invalid"` and the complaint, never a validation traceback. Only an unknown `kind` is an
  error instead, because the kind chooses the shape. The verify codes and the tiers are in
  [graph.md](graph.md).
- There is deliberately no tool for `accept`, `revert`, `pin` or `prune`: activating a correction is
  a human step, and `auditr graph refinements` is where it happens.
- Staged proposals live in the server process, so one run is opened, filled and committed through
  one server. `graph_refine_status` reports `staged_here: false` in any other process.
  `AUDITOR_REFINE_RUN` pre-binds every tool to one run, which is how a runner-spawned server works
  without passing `run_id` on each call.
- `graph_refinements` and `graph_log` answer the newest rows first, capped by `limit` (default 50,
  at most 500), with the total the same filters match as `refinement_count` / `run_count` and a
  `truncated` flag. `filtered` is true only when the caller narrowed the page, and `graph_log`'s
  `narrowed_by` names which filters did it; what the default run view hides on its own is reported
  apart as `hidden_statuses` and `hidden_count`.
- A run's `refinements` is `{committed, rejected}`, the same split `graph_refine_status` reports and
  the same one the run's `summary` line counts. Every run row carries `trigger_detail` with five
  keys: `files` and `targets`, the paths the trigger named and the node pairs the run was asked
  about, each capped at 10; `file_count` and `target_count`, the full sizes behind those caps; and
  `assessment` when there is one, travelling as counts rather than node ids.

### Proposing a knob change

- `propose_tuning(key, value, reason, path=".", client="cli")` records one repo-specific stopword
  as a `pending` row and applies nothing. A human runs
  `auditr graph tuning accept <id> --token <word>` after reading the trial.
- It is not a `graph_refine_*` tool. A tuning proposal is not a refinement (spec 5.4): it shares
  neither a run's staging nor the AST verifier, so there is no run to open, fill and commit.
- `key` must be allow-listed and only `stopwords` is shipped, so `value` is one lowercase token,
  letter first, at most 40 characters. `name_similarity_threshold`, `cluster_floor` and `knn_k` are
  declared and refused with the measurement that deferred them.
- `reason` is required and is what the human reads.
- Refusals come back as errors naming the cause: tuning is off, the key is not tunable or not
  shipped, the value is not one token, the reason is empty, the cap is full, the token is already
  active, or one proposal for this key was already recorded inside the last day.
- The returned row carries `token`, the confirmation word the accept has to repeat, plus
  `tuning_id`, `key`, `value`, `status`, `reason`, `run_id`, `created_at` and `allow_list`.
  `value` is the decoded token, so it can be handed straight back to
  `auditr graph tuning accept <value> --token <word>`.
- The trial that measures the proposal is a separate step, because a facts-only rebuild is tens of
  seconds. The observer runs it, or `auditr graph tuning measure <id>` does when no daemon is
  attached. It needs a built graph: on a checkout with none the trial records that and refuses once.

### Annotations and the shared preamble

- Every tool is annotated so clients can skip confirmation prompts and cache results: read-only for
  everything that only reads, mutating for `ignore_add`, `graph_build`, `malware_update_dbs`,
  `malware_install` and every refinement tool that writes, destructive for `ignore_remove`.
- `graph_refine_brief` counts as mutating because it records the run's prompt, though a re-read
  writes nothing. `graph_refine_abort` is only mutating because it drops staging that was never
  written. `graph_refine_begin`, `graph_refine_propose` and `graph_refine` are additionally marked
  non-idempotent, so a client must not silently retry them. No tool touches an open world.
- Every tool resolves its project root, loads the repo policy and opens the shared index through one
  seam, so a tool always addresses the same checkout identity the CLI does. A repo whose
  configuration does not load comes back as a one-line tool error naming the offending key.
- `graph_build` waits at most `graph.rebuild_lock_timeout_seconds` for another process's build
  before it comes back as a tool error naming the lock file, so a wedged build is never a hung call.

## Output format

- `scan` and `report` default to `detail="compact"`: a top-level `rules` map emitted once
  (`rule_id` to category, verdict kind, checklist item, standard refs, suggestion), and slim
  per-finding objects of `{rule_id, severity, line, message}`. Per-finding `evidence` and repeated
  rule metadata are dropped.
- `detail="summary"` returns counts only (`totals`, `by_rule`, `by_file`). `detail="full"` returns
  every field on every finding, and because that payload is large it comes back as a
  `ResourceLink` to read on demand rather than inline. `aggregate` returns one too.
- `limit` (compact only, default 50) caps the response to the worst findings and reports the
  surplus under `omitted`.
- `finding_detail(file, rule_id, line)` is the recovery path for one finding's `evidence`,
  `suggestion` and `standard_refs` after compact mode dropped them.
- A response-limiting middleware caps any single tool response at 500,000 bytes as a backstop.
  Resource reads, where the full artifacts live, are never truncated.
- A second middleware notes the config keys no model declares on stderr, once per repo the server
  is asked about. It reads that repo from the call's own `path`, `file` or `root` argument, most
  specific first, so it is skipped for the tools that declare none of the three (`malware_status`,
  `malware_install`). It never writes to stdout, where the protocol lives, and never fails a tool
  call.
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
