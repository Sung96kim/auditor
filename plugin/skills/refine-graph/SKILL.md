---
name: refine-graph
description: Run one graph refinement pass over the unresolved queue and review what it proposed. Use when asked to refine the code graph, work the unresolved queue, or accept or revert refinements.
---

Refine this repo's semantic graph: turn the questions the deterministic resolver left open into
verified edges and annotations.

## Look before you run

```bash
auditr graph unresolved --json          # the queue: what is open, on which node, and why
auditr graph refinements list --json    # what is already proposed, active, stale or reverted
auditr graph log --json                 # every run, its cost and its outcome
```

## Run one pass

```bash
auditr graph refine --json              # one run over the whole queue, configured runner
auditr graph refine pkg/ --json         # one run scoped to a path prefix (positional)
auditr graph refine pkg/ --brief        # render what that run would be asked, open nothing
```

- A run costs money. `auditr graph log --json` shows what the day has already spent, and the
  budget knobs are in [configuration.md](../../../docs/references/configuration.md).
- Every proposal is verified against the extracted facts before it is stored. `unverified` is a
  kind the verifier has no check for, not a failure.
- Refinements are an overlay. The build applies the `active` and `pinned` ones and nothing else, so
  reverting one is a status change and never a rebuild.

## Review what it proposed

```bash
auditr graph refinements list --status pending --json   # what is waiting on a human
auditr graph refinements accept <id>    # activate one; the next build applies it
auditr graph refinements revert <id>    # take one back out of the overlay
auditr graph refinements pin <id>       # keep one through anchor drift and dead builds
```

Recommend; do not apply on the user's behalf. Say which refinements you would keep, which you would
revert, and the code that makes you say so.
