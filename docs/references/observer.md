# observer

The background daemon that watches configured repos and serves the page a later slice will make
live. One process per `$AUDITOR_HOME`, reachable on loopback in about a millisecond, so a session
hook can post an edit without waiting on anything.

The daemon accepts, records and reports. It does not yet decide what to refine: the loop that reads
the spool and starts runs is a later slice, so today's consumer counts each drained batch.

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

## What it adds under the home

```
$AUDITOR_HOME/
  observer/
    lock                       # the singleton flock; whoever holds it is the daemon
    daemon.json                # {pid, port, home, version, compat}, written atomically
    log/observer.log           # both logging stacks plus the child's stderr, rotated at 5 MB
    locks/                     # NOT the daemon's: the graph rebuild lock owns this
  repos/<repo_dir_key>/
    spool.jsonl                # one repo's accepted, unconsumed events
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
  either. The next daemon adopts every `spool.jsonl` and every `spool.draining` it finds at start,
  oldest batch first.

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
  so a matching `If-None-Match` costs a 304 and skips the page query; the tag's own two reads are
  paid on every poll. `/api/runs`' tag names the repo, so two repos with the same run count do
  not share one.
- Every route with a `repo` in it answers `400` unless the query names an absolute directory. A
  relative name and no name at all both fall back to the daemon's own working directory, which is
  a repo the caller never asked about, so neither is answered.
- `POST /events` takes the `key` a hook already computed, and it must be a `repo_dir_key`: 40 hex
  characters, because it names the directory the spool is written to. Up to 2,000 paths per body;
  the shape filter runs once per path on the request thread, which is about 84 ms at the cap.
- `POST /admin/restart` takes `{compat, reason}`. A caller whose declared wire version this daemon
  already speaks is declined, so no local process can re-exec the daemon on its own say-so.
- `GET`, `HEAD`, `POST`, `PUT` and `DELETE` all answer JSON; an unknown route or method is a JSON
  404, never a stdlib HTML 501. A 304 sends the tag and no `Content-Length`: the length it would
  carry is zero, and a 304 names the cached body rather than describing its own.
- Every JSON shape is pinned by a committed schema under `tests/observer/schemas/`.
- The page is served at `GET /` and `HEAD /`, outside the API table. A HEAD answers the headers
  the GET would, its length included; every other method on `/` falls through to the table's 404.
  With no UI bundle built the page degrades to a plain status document naming the node, edge and
  cluster counts and how to run `pnpm build`, rather than raising. The bundle shipped today makes
  no HTTP request of its own; the polling page is a later slice.
- `/api/status` declares more than this slice fills. `state`, `idle_seconds`, `repos`,
  `drained_events`, `evals`, `vectors` and both meters are at their defaults until the repo loop
  lands; `home`, `version`, `compat`, `started_at`, `uptime_seconds`, `queued_repos` and
  `sessions` are the seven that are real.

## What the loop does

One `RepoLoop` per attached repo works spec 8.3's five items in order, highest first:

1. **Session-start build.** An incremental scan with extraction forced on, then a rebuild. A busy
   rebuild lock is logged and the attach still ends `observing`. Writes one `session_start` run
   row: `skipped`, no runner, zero cost.
2. **Edit batches.** Events collected per quiet window (`debounce_seconds`, last event wins), then
   spec 8.6's gate. A batch the gate passes opens a run over the pairs it chose; a batch it
   declines is still a run row. A batch whose every path stage 0 dropped writes nothing.
3. **Suspect drain.** `graph_unresolved` in the store's own priority order, minus the pairs on
   cooldown and the pairs an `unresolvable` or `redundant` refinement already answers. Item 2's
   deferred pairs are drained first.
4. **Verify runs.** A second opinion on `pending` refinements, shown the pairs and not the pending
   correction. It judges and stores nothing: agreement activates, a named different destination
   rejects, and silence leaves the row `pending`.
5. **Tuning trials.** The slot exists and returns 0; S11 fills it.

A run takes **pairs**, never a path prefix. `trigger_detail.targets` carries them, so `graph log
--json` and `GET /api/runs/<id>` both show what a run was asked about.

### The cooldown knob

- `observer.scheduling.cooldown_minutes` (default `60`): minutes a pair a run already named is
  skipped by the suspect drain.
- `0` opts out, which is what a repo whose whole queue fits in one run wants.
- Derived from `graph_runs`, not from a column on the queue: `graph_unresolved` is replaced
  wholesale by every build.

### The three pauses

- `paused:budget` while the day's cost or run ceiling is spent. Clears when the day window rolls.
- `paused:ratelimit` when a run reported one. Clears at the instant the SDK named, or five minutes
  on if it named none.
- `paused:auth` when a run could not authenticate. Clears after 15 minutes, or the moment any run
  reaches the model.

None of the three is written to disk. Auth outranks the other two, so a loop that cannot log in
never reports a budget it can never spend. A batch drained while paused is held and assessed when
the pause lifts.

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
