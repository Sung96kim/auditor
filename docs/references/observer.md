# observer

The background daemon that watches configured repos and serves the live page. One process per
`$AUDITOR_HOME`, reachable on loopback in about a millisecond, so a session hook can post an edit
without waiting on anything.

The daemon accepts, records and reports. It does not yet decide what to refine: the loop that reads
the spool and starts runs is a later slice, so today's consumer counts each drained batch.

## The five verbs

```bash
auditr observer start          # launch the daemon for this home (no-op if one is running)
auditr observer status --json  # where the daemon is, as JSON
auditr observer stop           # ask it to exit, then wait for the lock to free
auditr observer open           # open the live page in a browser
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
  not a stale lock, and liveness is always "can I take it", never a pid check.
- `spool.jsonl` is the durable half of `POST /events`: an event is on disk before the 202 returns,
  so a daemon killed mid-flight loses nothing. The next daemon adopts every spool it finds at start.

## The port

- `AUDITOR_OBSERVER_PORT` wins if it is set to an integer.
- Otherwise the port is `7490 + crc32(resolved $AUDITOR_HOME) % 500`, so one home is always one
  port and two homes almost never collide.
- The home is resolved first, so `~/.auditor` and `/home/you/.auditor` are one daemon rather than
  two.
- A value that is not an integer is ignored rather than fatal.

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

- The two ETag routes are the ones the page polls every 3 seconds. The tag is computed before the
  handler runs, so a matching `If-None-Match` costs a 304 and no database query.
- Every JSON shape is pinned by a committed schema under `tests/observer/schemas/`.
- The page is served at `GET /`, outside the API table. With no UI bundle built it degrades to a
  plain status document naming the node, edge and cluster counts and how to run `pnpm build`,
  rather than raising.

## Stopping

- `auditr observer stop` sends SIGTERM. The daemon releases the lock, removes `daemon.json` and
  exits.
- It also stops itself once `observer.scheduling.idle_shutdown_minutes` has passed with no request
  **and** no session is attached. `0` means it never exits on idle.
- `POST /admin/restart` makes it re-exec its own install spec, resolved fresh through
  `shutil.which("auditr")`, so an upgrade takes effect. There is no drain window: spooled events
  survive on disk and the new daemon adopts them.
