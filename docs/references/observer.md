# observer

The background daemon that watches configured repos and serves the live page. One process per
`$AUDITOR_HOME`, reachable on loopback in about a millisecond, so a session hook can post an edit
without waiting on anything.

The daemon accepts, records and refines. One `RepoLoop` per attached repo drives spec 8.3's ladder
over the events the drain hands it, and `/api/status` counts those events, not batches, as
`drained_events`.

## The five verbs

```bash
auditr observer start          # launch the daemon for this home (no-op if one is running)
auditr observer status --json  # where the daemon is, as JSON
auditr observer stop           # ask it to exit, then wait for the lock to free
auditr observer open           # open the daemon's page in a browser
auditr observer ensure         # start one if there is none, restart one whose wire is stale
```

- Every verb answers the same shape, `DaemonStatus`: `running`, `action`, `pid`, `port`, `home`,
  `version`, `compat`, `page_url`.
- None of them exits non-zero. A hook can call any of them without risking the session.
- `auditr-observer` is the same command surface as a standalone console script. It is stdlib-only
  and imports nothing from `auditor`, so a hook pays about 0.02 s instead of about 0.17 s. It
  starts the daemon by running `auditr observer start` rather than by re-implementing it.
- `auditr observer start --foreground` is the daemon itself, hidden. It is what the launcher spawns
  and what a restart re-execs.

## The hook client

`auditr-observer hook <session-start|post-tool-use|stop|session-end> --client <claude-code|codex>`
reads that client's own hook JSON on stdin and posts what it means. It is the only place the
observer's transport, kill switch, Stage 0 filter and spool exist; the Claude plugin's scripts
shell out to it and hold none of them.

Codex has no plugin-loaded hooks, so its two events are installed by hand. Copy this into
`~/.codex/hooks.json`, or into a trusted project's `.codex/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "auditr-observer hook session-start --client codex" }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "auditr-observer hook stop --client codex" }
        ]
      }
    ]
  }
}
```

Two events, not four, and that is the whole story Codex can tell:

- There is no `SessionEnd`, so a Codex session is reaped by expiry rather than detached.
- There is no per-edit hook. The only `tool_name` Codex dispatches is `Bash`, whose `tool_input`
  is a command and never a path, so `PostToolUse` has nothing to post. Every Codex edit reaches
  the daemon through the `Stop` batch's `git status` set instead, which means the observer sees a
  Codex turn's edits when the turn ends rather than as they happen.
- `features.codex_hooks` is a feature flag and can be off. With it off a Codex session produces no
  events at all and the daemon simply never hears about that repo; `codex features list` says
  which state a machine is in.
- `AUDITOR_OBSERVER=0` switches the whole client off, Codex included, and every verb still exits
  0. That matters more here than on the Claude side: Codex runs the command straight out of
  `hooks.json` with no wrapper script to swallow a failure.

### Verifying the Codex runner against a real account

Nothing in the test suite or the dogfood starts a Codex session, so the first real run is a
deliberate act. It costs money and it writes to your graph. Before it:

```bash
# 1. see what the runner would be given, with no session opened
auditr graph refine <scope> . --brief

# 2. pin a model, so the run is priced rather than run-bounded
#    (edit observer.runner.codex_model in the user config)

# 3. one run, one turn, read-only sandbox
auditr graph refine <scope> . --runner codex --json
```

Afterwards, four things are worth reading:

- `auditr graph log . --json`: the row's `runner`, `status`, `usage.cost_usd` and
  `usage.cost_estimated`, which is always true for Codex.
- `$AUDITOR_HOME/observer/codex-home/config.toml`: exactly one `[mcp_servers.graph]`, pointing at
  a loopback URL, and `features.codex_hooks = false`.
- `$AUDITOR_HOME/observer/codex-home/auth.json`: a symlink, never a copy.
- `auditr graph refinements . --json`: what it staged, and whether the verifier agreed.

A run that ends `aborted` with `refused: unexpected mcp servers` means something above
`CODEX_HOME` added a server: check `/etc/codex/config.toml`. One that ends `aborted` with
`over_budget` means the estimate passed `observer.budget.max_budget_usd_per_run`.

| event | what it does |
|---|---|
| `session-start` | `ensure` (starts a daemon if there is none), then `POST /sessions/attach` with a 3 s budget |
| `post-tool-use` | Stage 0 on the one path the Edit or Write named, then `POST /events` with `kind="edit"` |
| `stop` | `POST /sessions/heartbeat`, an attach when that answers `ok: false`, then the whole `git status --porcelain=v1 -z --untracked-files=all --ignore-submodules=all` path set as `kind="stop"` |
| `session-end` | `POST /sessions/detach` |

- A `post-tool-use` event carrying `agent_id` is dropped: that is a subagent's tool call, not
  this session's edit. The three lifecycle events are not gated on it.
- Hook-side Stage 0 is the config-free half only, a suffix or filename allowlist plus the excluded
  directory names. It can only drop paths the daemon's own `FileDiscovery.auditable_shape` would
  also drop, so a drop is a saved round trip and never a lost edit.
- Every path posted is repo-relative. A relative name is anchored at the session's `cwd` and an
  absolute one is made relative to the repo root; a path that is under neither has no
  repo-relative spelling and is not posted at all. That shape is the only one the graph is keyed
  on, so anything else would name a file the index has never seen.
- A batch is truncated at 2,000 paths, the wire's own cap. A longer body is refused whole, and a
  refusal is dropped rather than kept, so the tail of one Stop batch is the cheaper loss.
- An edit event has a 200 ms wire budget, the session-start attach 3 s, the attach that repairs
  a lost session 1 s, and a Stop batch 2 s, because the daemon runs its own Stage 0 once per path
  on the request thread before it answers. A cold daemon launch can outrun the session-start
  budget, in which case the session is not attached yet; the next `Stop` heartbeat answers
  `ok: false` and the client attaches there, so the lag is one turn and not one session.
- The git subprocesses are budgeted too, and they are the larger half: 500 ms for each
  `git rev-parse` that resolves the repo identity and 2 s for the `git status` behind a Stop path
  set. They run *before* the batch reaches the spool, so a hook killed inside one loses the batch
  outright, and the plugin script's own kill deadline has to cover the whole ladder:
  `HOOK_BUDGETS` sums it to 1.2 s for an edit and 6.2 s for a Stop, against `OBSERVE_TIMEOUT`s of
  2 s and 8 s.
- **The batch is written to the spool before it is posted, not after.** The hook's parent kills it
  on a timeout, and only a batch already on disk survives that. Each batch is its own file,
  `repos/<repo_dir_key>/spool.client.<batch>.jsonl`, with a `root.json` breadcrumb beside it, and
  it carries a `batch` id. What the answer means:
  - a 2xx took it, so the file is deleted;
  - a 400, 403 or 413 is this daemon refusing this body, which no retry changes, so the file is
    deleted too;
  - any other 4xx is not this daemon's answer at all - a 404 is what a stranger on a recycled
    port, or a daemon from a release without `/events`, replies - so the file stays;
  - a 5xx is the daemon failing rather than refusing, so the file stays;
  - nothing at all, including the client being killed, leaves the file where it is.
  A daemon adopts those files at start and on every drain, and it drops a `batch` id it has
  already drained, so a delivery whose answer outran the client's socket budget is not assessed
  twice. The client stops adding to a repo's spool past 128 undelivered batches.
- Every path is attributed to the **session's** repo, never the edited file's. An edit inside a
  nested checkout under the session root is posted under the outer repo's key, which is what
  `auditr scan` does with the same file.
- `AUDITOR_OBSERVER=0` is read before the verb runs, so the kill switch covers all four events.
- Nothing exits non-zero, whatever happens: an unexpected failure inside the verb is one line on
  stderr and an exit code of 0, and a `hook` verb run by hand from a terminal reads no payload
  rather than blocking on stdin.

## What it adds under the home

```
$AUDITOR_HOME/
  observer/
    lock                       # the singleton flock; whoever holds it is the daemon
    daemon.json                # {pid, port, home, version, compat}, written atomically
    log/observer.log           # both logging stacks plus the child's stderr, rotated at 5 MB
    locks/                     # NOT the daemon's: the graph rebuild lock owns this
  repos/<repo_dir_key>/
    root.json                  # the crumb that gives an adopted spool a repo root
    spool.jsonl                # one repo's accepted, unconsumed events
    spool.client.<batch>.jsonl # one batch the hook client wrote and could not deliver
    status.json                # the scan and graph blocks the status line renders
```

- The daemon creates only `lock`, `daemon.json` and `log/`. It never creates, clears or replaces
  `observer/` itself, because `observer/locks/` belongs to the graph rebuild lock and predates it.
- The lock file is never deleted. The kernel frees a flock when the holder dies, so a stale file is
  not a stale lock. The daemon itself takes the lock to be the singleton; a client asks `/health`
  instead, because probing the flock meant acquiring it and could take it from a daemon that was
  still starting.
- `spool.jsonl` is the durable half of `POST /events`: an event is on disk before the 202 returns,
  so a daemon killed mid-flight loses nothing. A drain takes the file by rename to `spool.draining`
  and unlinks it only once its consumer has returned, so a kill in that window loses nothing
  either. The next daemon adopts every `spool.jsonl`, every `spool.client.<batch>.jsonl` and every
  `.draining` of both that it finds at start, oldest batch first. The client writes its own files
  rather than appending to `spool.jsonl` because the daemon renames that one out from under a
  writer on every drain, and because one file per batch is what makes delete-on-2xx a single
  unlink.
- An adopted spool goes through the same gate `POST /sessions/attach` does before it gets a loop.
  A repo that never opted in is left with its spool on disk and no loop, so spooling an event at a
  daemon that is not running is not a way past the gate.
- The daemon writes the `graph` block through `auditor.status.write_block`, which files the
  frozen `GraphStatusBlock` under the key the model names and read-merge-replaces that one block
  under a lock, so a concurrent `auditr scan` writing `scan` cannot lose it and it cannot lose
  the scan's.
- A retired repo's meters and block stamp are dropped when its key is retired, and again when its
  driver ends if nothing has claimed the key since, so a row the switcher still lists cannot draw
  a live budget bar under an empty state badge, and a re-adopted key keeps its new loop's meters.

## The port

- `AUDITOR_OBSERVER_PORT` wins if it is set to an integer in `0..65535`; `0` asks the kernel for
  any free port, and anything else falls back to the hash rather than raising.
- Otherwise the port is `7490 + crc32(resolved $AUDITOR_HOME) % 500`, so one home is always one
  port and two homes almost never collide.
- The home is resolved first, so `~/.auditor` and `/home/you/.auditor` are one daemon rather than
  two.
- A value that is not an integer, or is outside `1..65535`, is ignored rather than fatal.
- A port already in use ends the start with a line in `observer.log` and the lock given back,
  rather than a traceback from a half-started daemon.

## The kill switch

- `AUDITOR_OBSERVER=0` turns the observer off. Every verb prints a notice and exits 0.
- The off values are `0`, `f`, `false`, `n`, `no`, `off`, case-insensitive. The client and the
  package read the same set, so one cannot be off while the other is on.
- Any other value leaves it on. A typo cannot take an unrelated `auditr` command down.

## When a session may attach

`POST /sessions/attach` is an AND gate. The first clause that refuses is the reason reported:

1. The caller named a home, and it is this daemon's home.
2. The repo is configured for auditor.
3. The repo did not set `observer_allowed = false`.
4. `observer.enabled` is true in user settings.
5. The repo is the main worktree, or `observer.worktrees` is `all`.

`graph.enabled` is deliberately not a clause. Sessions expire 45 minutes after their last
heartbeat, decided when they are next read rather than by a timer.

## The API

Loopback only, `127.0.0.1`. There is no authentication, because nothing off this machine can reach
it. A graph and a verbatim prompt never leave the host.

Binding to loopback stops other hosts, not other origins, so the transport refuses before it
dispatches:

- any request carrying an `Origin` header, with `403`, because a `text/plain` POST is a CORS
  simple request and any page the user visits could otherwise reach the side-effecting routes;
- any `Host` that is not loopback, with `403`, which is what a DNS-rebinding page sends;
- any request carrying `Transfer-Encoding`, with `411`, because the chunks would stay in the
  socket and parse as the next request line;
- a `Content-Length` that is unreadable or negative, with `400`, and one over 1 MiB with `413`.

A connection has a 10 s deadline, so a client that declares a body and never sends it frees its
thread rather than pinning one.

| Route | Answers | ETag |
| --- | --- | --- |
| `GET /health` | home, db path, version, wire compat | no |
| `GET /api/status` | the state badge, meters, sessions and counters | yes |
| `GET /api/repos` | the repo switcher's list | no |
| `GET /api/graph` | the visualization document for one repo | no |
| `GET /api/runs` | one page of the run stream | yes |
| `GET /api/runs/<id>` | one run: prompt, tool trace, refinements, trials | no |
| `GET /api/refinements` | the refinement list by status | no |
| `GET /api/evals` | the latest eval row per runner | no |
| `GET /api/flow` | one flow walk | no |
| `POST /events` | 202 once the batch is spooled | no |
| `POST /sessions/attach` | `{attached, reason, page_url}` | no |
| `POST /sessions/heartbeat` | `{ok, reason}` | no |
| `POST /sessions/detach` | `{ok, reason}` | no |
| `POST /admin/restart` | `{restarting, reason}` | no |

- The two ETag routes are the two a page would poll. The tag is computed before the handler runs,
  so a matching `If-None-Match` costs a 304 and skips the page query; the tag's own three
  aggregates are paid on every poll. `/api/runs`' tag names the repo, so two repos with the same
  run count do not share one, and it carries the ledger's last change as well as its count and
  its newest start, because a run is inserted once and then mutated in place: without that a run
  going from `queued` to `succeeded` never reaches an open page.
- Every route with a `repo` in it answers `400` unless the query names an absolute directory. A
  relative name and no name at all both fall back to the daemon's own working directory, which is
  a repo the caller never asked about, so neither is answered.
- `POST /events` takes the `key` a hook already computed, and it must be a `repo_dir_key`: 40 hex
  characters, because it names the directory the spool is written to. It also takes the client's
  own `batch` id, which is what a redelivery is recognised by. Up to 2,000 paths per body; the
  shape filter runs once per path on the request thread, which is 127 ms median at the cap on the
  machine this was last measured on, and has been seen at 900 ms on a slower one.
- `POST /admin/restart` takes `{compat, reason}`. A caller whose declared wire version this daemon
  already speaks is declined, so no local process can re-exec the daemon on its own say-so.
- `GET`, `HEAD`, `POST`, `PUT` and `DELETE` all answer JSON; an unknown route or method is a JSON
  404, never a stdlib HTML 501. A 304 sends the tag and no `Content-Length`: the length it would
  carry is zero, and a 304 names the cached body rather than describing its own.
- Every JSON shape is pinned by a committed schema under `tests/observer/schemas/`.
- Two routes take query parameters beyond `repo`:

  ```
  GET /api/runs   repo (required), skipped=1, status=a,b, since=90s|2h|7d|ISO, limit=N
  GET /api/flow   repo (required), symbol, direction=out|in, depth=N, limit=N, expand_hubs=1
  ```

  An unusable value is a 400 naming the field, never a 500 and never `int()`'s own message; a
  query the handler will refuse produces no ETag, so a stale `If-None-Match` cannot turn it into a
  304. A control named with no value at all is one of those: `?depth=` and `?status=` are typos
  rather than requests for the default, and all five controls read them the same way. An out-of-range `depth` or `limit` is clamped rather than refused, because `FlowOptions.of`
  clamps by design. `expand_hubs=1` walks past every elided hub instead of stopping at it, which
  is how the page's hub disclosure gets children to draw; `limit` still bounds the walk. `/api/runs`' ETag covers the filter, `since` included, so two windows over one
  ledger never share a tag while each still 304s on its own; `since` is fingerprinted as the raw
  query value rather than the epoch it resolves to, or a window would mint a new tag every request.
- The page is served at `GET /` and `HEAD /`, outside the API table. A HEAD answers the headers
  the GET would, its length included; every other method on `/` falls through to the table's 404.
  Its `repo` goes through the same guard the API routes use, so a name that is not an absolute
  directory draws the no-repo page rather than opening a store handle per distinct string.
  With no UI bundle built the page degrades to a plain status document naming the node, edge and
  cluster counts and how to run `pnpm build`, rather than raising. The daemon injects
  `window.__AUDITOR_OBSERVER__ = {live, base, repo}` beside `window.__AUDITOR_GRAPH__`, with every
  `<` in either blob escaped, so no value in them can end the element or open the tokenizer's
  double-escaped state. The page reads that flag at first paint and then polls `/api/status` and
  `/api/runs` every 3 s with `If-None-Match`. `graph serve` injects no bootstrap, so the same bundle stays a static snapshot
  there and issues no request at all. The page is read-only by transport: a browser sends `Origin`
  on a same-origin `POST` and the server refuses any request that carries one, so nothing on the
  page can write.
- `/api/status`'s `state` is the daemon's own word, `running` or `restarting`; the per-repo state
  badge reads `repos[i].state` instead, which is that repo's `LoopState`. The badge adds two words
  of its own for a repo with no roster row: `no repo` when the URL names none, and `not tracked`
  when it names one the daemon serves a graph for but has never attached. `idle_seconds` is the gap
  before the request being served, measured from the daemon's start until something arrives. A read
  is never activity: no `GET` or `HEAD` moves it, whatever the route, so no page fetch and no
  status call can hold the daemon open past the idle window. Only a write does.
- `evals` is the runner roster, one row per model runner carrying its name and the model it is
  pinned to with no measurements in it; `/api/evals` is the per-repo answer that fills the numbers,
  and the page fetches both: the roster lays the block out at first paint and decides its rows,
  the measurements route supplies each stratum's 95% lower bound.
  Both resolve a runner's model the same way, so a runner with no model of its own carries an
  empty string and no numbers on either route, rather than another runner's.
- `vectors` is still at its default and stays there until S13. Both meters are real, and they are
  per repo: `budget` and `limits` ride on each `repos[]` entry, carrying what that repo's own loop
  published, so two repos cannot overwrite one another's numbers. The budget bar and its caption
  both follow `budget.priced`: dollars against `max_cost_usd_per_day` when it is true, runs against
  `max_runs_per_day` when it is false, which is the same ceiling `remaining_fraction` is measured
  from.

## What the loop does

One `RepoLoop` per attached repo works spec 8.3's five items in order, highest first:

1. **Session-start build.** An incremental scan with extraction forced on, then a rebuild. A busy
   rebuild lock is logged and the attach still ends `observing`. Writes one `session_start` run
   row: `skipped`, no runner, zero cost.
2. **Edit batches.** Events collected per quiet window (`debounce_seconds`, last event wins, the
   window restarting at most five times), then spec 8.6's gate. A batch the gate passes opens a run
   over the pairs it chose; a batch it declines is still a run row. A batch whose every path stage 0
   dropped writes nothing.
3. **Suspect drain.** `graph_unresolved` in the store's own priority order, minus the pairs on
   cooldown and the pairs an `unresolvable` or `redundant` refinement already answers. Item 2's
   deferred pairs are drained first.
4. **Verify runs.** A second opinion on `pending` refinements, shown the pairs and not the pending
   correction. Its proposals are judged and never stored, but the judgement moves the row it was
   asked about: agreement activates it, a named different destination rejects it, and silence
   leaves it `pending`.
5. **Tuning trials.** The slot exists and returns 0; S11 fills it.

A run takes **pairs**, never a path prefix. `trigger_detail.targets` carries them, so `graph log
--json` and `GET /api/runs/<id>` both show what a run was asked about.

An edit batch orders its targets on four keys. Proximity first: a question in a file the batch
edited, then one in a directory it edited. Then a `bare` or `self` call form. Then the newest staled
refinement anchored to the node, and last the queue's own drain priority.
`observer.limits.max_nodes_per_run` caps the list and counts distinct nodes, so a second question
about a node already taken rides along free. What the cap leaves over is deferred, and item 3 drains
it first.

### The cooldown knob

- `observer.scheduling.cooldown_minutes` (default `60`): minutes a pair a run already named is
  skipped by the suspect drain.
- `0` opts out, which is what a repo whose whole queue fits in one run wants.
- Derived from `graph_runs`, not from a column on the queue: `graph_unresolved` is replaced
  wholesale by every build.

### The four pauses

- `paused:budget` while the day's cost or run ceiling is spent. Clears when the day window rolls.
- `paused:ratelimit` when a run reported one. Clears at the instant the SDK named, or five minutes
  on if it named none.
- `paused:auth` when a run could not authenticate. Clears after 15 minutes, or the moment any run
  reaches the model.
- `paused:error` when a pass raised. Clears after `error_backoff_seconds`, doubling per consecutive
  failure up to `max_error_backoff_seconds`; a pass that finishes clears the count.

None of the four is written to disk. The error hold outranks the rest, because a loop whose last
pass raised has no answer about anything else, then auth, so a loop that cannot log in never
reports a budget it can never spend. A batch drained while paused is held and assessed when the
pause lifts; the hold keeps at most 500 events and drops the oldest first.

## Stopping

- `auditr observer stop` sends SIGTERM. The daemon releases the lock, removes `daemon.json` and
  exits.
- It also stops itself once `observer.scheduling.idle_shutdown_minutes` has passed with no request
  **and** no session is attached. `0` means it never exits on idle.
- `POST /admin/restart` makes it re-exec its own install spec, resolved fresh through
  `shutil.which("auditr")`, so an upgrade takes effect. There is no drain window: spooled events
  survive on disk and the new daemon adopts them. `ensure` on both front doors is what calls it:
  it reads `compat` from `/health`, and on a mismatch asks for the restart and waits for the
  daemon whose `started_at` moved, because the pid survives the exec.
