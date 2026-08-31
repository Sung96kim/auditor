---
name: graph-observer
description: Inspect and control the observer daemon: what it is watching, what it refined, what it skipped and why, and how to turn it off. Use when asked about the observer, the graph status line segment, or the daemon.
---

Report on the background daemon that refines this repo's graph while the user works.

## Is it running

```bash
auditr observer status --json   # running, pid, port, home, wire version, page URL
auditr observer start           # launch one for this home (no-op if there is one)
auditr observer stop            # ask it to exit
auditr observer open            # open its page in a browser
```

`AUDITOR_OBSERVER=0` disables it outright, everywhere: the daemon, the CLI verbs and the four
session hooks. That is the switch to reach for when a user wants it off, not uninstalling anything.

## What has it done

```bash
auditr graph log --json             # every run: trigger, runner, cost, outcome
auditr graph log --skipped --json   # the batches it assessed and declined, with the reason
auditr graph refinements list --json
```

A `skipped` row is not a failure. The observer assesses every edit batch before it spends anything,
and most batches are correctly declined; the reason on the row says which stage stopped it.

## The status line

`◆ graph 1.2k · 7 refined · observing` is the daemon's own segment: the size of the graph, the
refinements the build currently applies, and the repo loop's state. `◆ graph off`, dim, means the
daemon is not running or its last report is older than `session_expiry_minutes`. No segment at all
means this repo has never been observed.

## When it is not attaching

The gate is an AND: the home matches, the repo is configured, `observer_allowed` is true in the
repo's own `[tool.auditor]`, `observer.enabled` is true in the user settings, and this is the main
worktree unless `worktrees` is `all`. The refusal names exactly one clause, and the place to read
it is the daemon's own log under `$AUDITOR_HOME/observer/log/`: the hook that asked discards the
answer, and `auditr observer status` reports the daemon, not any one repo's attach. Full clause
order in [observer.md](../../../docs/references/observer.md).
