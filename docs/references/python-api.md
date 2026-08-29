# Python API reference

`auditor` is importable as a library: the same engine the CLI and the MCP server drive. The package
`__init__` exports one entry point for auditing, one for rendering, the configuration loader, the
index handle, and the record models. Importing `auditor.config` (which `auditor` itself imports)
registers every built-in detector, language auditor and reporter, so no bootstrap call is needed.

## Common invocations

```python
import asyncio
from pathlib import Path

from auditor import IndexStore, audit_target, load_config, render


async def main() -> None:
    # audit a directory and render the results as SARIF
    results = await audit_target(Path("src"), incremental=True)
    print(render(results, "sarif"))

    # the merged configuration for a repo, without auditing anything
    settings = load_config(Path("."))
    print(settings.extends)

    # open the shared index directly
    async with await IndexStore.connect(Path.home() / ".auditor/index.db", "/path/to/repo") as db:
        print(await db.repos.list())


asyncio.run(main())
```

## `audit_target`

- `async def audit_target(target: Path, *, ...) -> list[ScanResult]`. It resolves the project root,
  loads the config, optionally opens the shared index, and audits a file or a directory.
- The keyword arguments mirror the CLI flags: `incremental`, `no_index`, `strict_tests`,
  `allow_local_plugins`, `profile`, `exclude`, `no_skips`, `include_gitignored`, `report_only`,
  `root`, `config_overrides`, `apply_ignores`, `show_ignored`, `cross_file`, `progress`. See
  [scan.md](scan.md) for what each one changes.
- `report_only` (repo-relative paths) scopes the returned results without narrowing the scan, so
  cross-file rules stay correct. `root` pins the project root instead of searching for it.
- `config_overrides` is a dict deep-merged onto the loaded config as the highest layer, the same
  shape `--config-json` takes ([configuration.md](configuration.md)).
- It is async because the index is: call it from an event loop, not from module scope.

## `render`

- `render(results: list[ScanResult], fmt: str) -> str` renders a result list through the reporter
  registered for `fmt`.
- Built-in formats: `json`, `sarif`, `md`, `html`. A plugin can register more
  ([plugins.md](plugins.md)).
- An unknown `fmt` raises `ValueError` naming the available formats.

## `load_config`

- `load_config(root: Path, *, profile=None, allow_local_plugins=False, loader=None,
  overrides=None) -> AuditorSettings` returns the merged repo configuration. It is the only
  loader; there is no separate report call.
- It loads plugins between the raw read and validation, so a config may name plugin-contributed
  rules. `profile` replaces the repo's `extends` for this load.
- `settings.unknown_keys` is the tuple of dotted paths no model declares, filled at load time.
  Unknown keys never fail the load and the loader never warns; the CLI and the MCP server print
  them once on stderr.
- `unknown_keys` is excluded from every dump, so `model_dump()` and `auditr config show --json`
  carry the configuration only.
- `AuditorSettings.merged(raw)` is the classmethod that pairs a validated model with the unknown
  keys from the same raw dict, for a caller that merged the layers itself.
- `ResolvedConfig(settings, role=..., rel_path=...)` narrows those settings to one file;
  `.effective(rule_id)` returns the enablement, severity, verdict kind and thresholds that apply to
  one rule there.

## `IndexStore`

- `await IndexStore.connect(db_path, repo, partition=None)` opens the shared SQLite database and
  binds the handle to one repo's partition. It is an async context manager; `aclose()` stops the
  worker thread.
- `partition` is a `Partition(identity, prefix)`, the checkout every worktree shares plus that
  worktree's toplevel-relative prefix. Omitted, the repo key is the identity.
  `paths.partition_for(root)` builds one (cached per process).
- Per-table stores hang off the handle:
  - repo-scoped: `repos`, `ignores`, `files`, `findings`, `shapes`, `graph`.
  - identity-scoped: `runs`, `refinements`, `tuning`, `evals`.
- `await index.transaction(fn)` runs `fn(conn)` on the live connection as one commit and rolls
  back on any exception. It is what a build uses to land nodes, edges, the queue and the findings
  together:

```python
# one commit: either every write lands or none does
async with await IndexStore.connect(db_path, repo) as index:
    await index.transaction(lambda conn: write.apply(conn, index))
```

- The `write_*` methods (`graph.write_graph`, `graph.write_unresolved`, `findings.write_add`,
  `findings.write_clear_for_rules`, `refinements.write_outcomes`) are the halves a transaction
  composes. They take the open connection and never commit.
- Where the database lives and how it is partitioned is in [index.md](index.md).

## Graph refinement

- `auditor.graph.refine.service.RefinementService(index, root, settings, user, registry=None)`:
  `begin`, `propose`, `brief`, `preview`, `build_brief`, `status`, `commit`, `abort`, `terminate`,
  `prune`, `rebuild`. `brief` renders an open run's brief and records it on the row the first time
  it is read; `preview` renders the same brief for a scope without opening a run. `commit`, `abort`
  and `terminate` all take an optional `RunAttribution` (the cost, the tool trace, the SDK session
  and the producer's own one-line summary). The only supported way to record a graph correction; it
  is what the `graph_refine_*` MCP tools call. `registry` defaults to the registry this process
  shares for that repo identity, and passing a fresh one splits the staging.
- `auditor.graph.refine.service.RefinementLedger(index=...)`: `accept`, `revert`, `pin`, `prune`,
  `refinement`. The by-hand half of the lifecycle, over an index handle alone. It needs no checkout
  root and no run, which is what `auditr graph refinements accept <id>` has to work without.
- `auditor.graph.refine.verify.FactVerifier`: the AST-fact check one proposal has to pass. Pure, so
  a caller reads the files and hands the facts in. `auditor.graph.refine.facts.FactReader` is the
  reader that does that reading, and the brief builder shares it.
- `FactReader.queue(prefix, limit=..., external=...)` and `FactReader.count_queue(...)`: the one
  queue read the brief and the verifier share. A reader holding `synthetic` rows answers from them
  and nothing else, which is how an eval masks an edge without the stored queue filling the gap.
  `RefinementService(..., facts=reader)` injects one.
- `auditor.graph.refine.facts.BriefBuilder(facts=..., limits=...)`: `build(scope, commit_sha=...)`
  returns the `Brief` a run works from, and `Brief.render()` is the prompt text itself, pinned by a
  golden file. The models it builds live in `auditor.graph.refine.brief`, which reads nothing.
- `auditor.graph.refine.runner.RefinementRunner`: the producer ABC. `run(job)` opens a run, records
  its brief, works, and closes it, answering with a `RunProduct` (the run row it opened, the brief
  and what the commit landed). How the run ended is on the stored row, not on the product, so the
  two cannot disagree. `FakeRunner` replays a scripted set of proposals, which is how the whole
  path is tested with no SDK installed. Every runner takes a `proposer`
  (`Callable[[str, Mapping[str, Any]], Awaitable[Verdict]]`), defaulting to the service's own
  `propose`; an eval passes its judge instead, so nothing it proposes reaches the ledger.
- `auditor.graph.refine.sdk_runner.SdkRunner`: the Claude producer. SDK-free by design: it talks to
  the client through an injected factory answering to `ClientSession`, so its message loop, init
  check and outcome mapping are all testable without the extra.
- `auditor.graph.refine.sdk_client.claude_client(options, tools)`: the only importer of
  `claude_agent_sdk`. Builds the real client and the in-process `graph` MCP server from an
  `SdkOptions` and the run's own `BoundTools.tools()` table.
- `auditor.graph.refine.drive`: `select_runner`, `build_runner`, `refine`, `brief` and `evaluate`.
  The one module the CLI and the MCP tools import from the runner half, so neither can drift on the
  runner choice or on the payload. It owns the single `observer-claude` import guard, and it is the
  only importer of `eval.py`.
- `auditor.graph.refine.eval`: what `auditr graph eval` measures.
  - `Population.of(facts)` reads the graph, resolves it again and keeps the edges tier B is
    measured on (`population.ground`), plus the collision rows, the decoy pool, the node kinds and
    the names this repo defines.
  - `Ground.excluded_by(rule)` answers which resolved edges each ground-truth rule left out.
  - `population.sample(suite=..., size=..., seed=...)` draws trials per stratum and
    `batches(trials, size)` groups them into runs that never mix strata.
  - `EvalSuiteSpec.of(suite)` is one suite's draw, verdict rule and strata. A suite is one
    subclass, registered by its own definition; an unhandled suite raises rather than measuring a
    different one.
  - `Judge.over(trials)` scores a batch's proposals without storing any, and never scores one its
    validators refused. `tally(judgements, suite=..., spend=..., off_target=...)` sums them.
  - `EvalRun(service=..., build=..., runner=..., model=..., size=..., seed=..., on_plan=...)`
    drives the whole thing: `await run.report(suites)` writes one `graph_evals` row per completely
    measured stratum, and `report(suites, dry_run=True)` answers with the plan and opens no run.
- `auditor.graph.refine.models.wilson_lower(correct, total, z=1.96)` and
  `flawless_floor(min_precision, z=1.96)`: the Wilson score interval's lower bound a tier gate
  reads, and the smallest flawless run that clears a given bar (73 at 0.95). `flawless_floor`
  searches to 10,000 trials and answers `None` beyond it, which every caller reads as "unprovable
  at this bar".
- `EvalsDB.latest(runner, model)` (`index.evals`): the newest `graph_evals` row per
  `(suite, stratum)`, which is what `TierPolicy.of` expects and what makes a regression un-prove a
  stratum.
- `auditor.graph.query.LogQuery(index)`: `page(spec)` for `graph log`'s two views and
  `refinements(statuses, limit)` for the corrections page. The one reader the CLI and the MCP tools
  both call, so the two surfaces cannot drift on ordering or on what a time window means. Both
  answer newest first.
- `RefinementService.decline(assessment, *, checkout=None, ...)`: the ledger's one assessment
  writer. Opens a `graph_runs` row with `runner=none` and closes it `skipped` in one call, staging
  nothing, so a skip can never evict a live run. Refuses an assessment whose decision is `run`.
  Pass a `Checkout` you already read and it spawns no `git rev-parse`.
- `RunsDB.spend_since(since) -> Spend` (`index.runs`): one SQL aggregate over this checkout's
  model-calling runs since a timestamp. Rows with `runner = none` are excluded, because an
  assessment spent nothing.

## Observer: the change assessment

Spec 8.6's gate, `auditor/observer/`. Every function here is pure: no store handle, no filesystem
read, no clock. The caller does the I/O and hands the values in.

- `auditor.observer.assess.assess_path(edited) -> PathVerdict` classifies one edited path against
  the facts cached for it, and `stage_one(edited) -> Stage1` does a whole batch, deduplicating by
  path, the last read of a path winning.
- `auditor.observer.assess.assess(stage1, *, before, after, scheduling, budget, max_nodes_per_run,
  flow_nodes=frozenset()) -> Assessment` is the whole thing for a batch that was rebuilt for;
  `assess_unchanged(stage1)` is the answer for one stage 1 dropped.
- `decide(*, new_pairs, bounded_pairs, stale_refinements, scheduling, budget, kind=BatchKind.EDIT)
  -> (Decision, pairs)` is the rule itself, public because a suspect batch and a verify batch gate
  against the same state; `kind` is what tells the three apart. It refuses a spent day for all of
  them, and applies the two low-budget rules to `edit` alone. The second half is the pairs a run
  would take, empty on every skip, which is what `deferred_pairs` is measured against.
- `narrowing(*, new_pairs, bounded_pairs, budget, kind=BatchKind.EDIT) -> (pairs, narrowed)` is the
  low-budget rule's one home, called once per assessment through `decide`.
- `new_rows(before, after)`, `new_pairs(before, after)`,
  `resolved_pairs(before, after, *, removed_nodes)` and
  `staled_refinements(before, after, *, changed_nodes)` are the stage 2 diffs. They compare on
  `graph_unresolved`'s whole key, `(node_id, name, reason)`, and report distinct `(node_id, name)`
  pairs, so a name asked twice for two reasons is two rows to diff and one question to count.
- Models: `PathOutcome` (six members), `NodeDigest`, `CachedFile`, `EditedFile`, `PathVerdict`,
  `Stage1`, `QueuePair`, `RefinementState`, `GraphSnapshot`.
  `QueuePair.of(row)` narrows an `UnresolvedRow` and `RefinementState.of(refinement)` reads a
  stored row's `anchored_ids()`.
- `auditor.observer.budget.budget_state(spend, *, config, priced=True, evaluated=False)` builds
  `BudgetState`; `window_start(now)` is `now - DAY_SECONDS`, a rolling 24 hours rather than a
  calendar day.
- The vocabulary the store shares lives in `auditor.graph.refine.models`: `Assessment`,
  `AssessmentDecision`, `BatchKind`, `Decision`, `NodePair` and `Spend`, plus
  `TriggerDetail.assessment`. `Decision` is the gate's answer and its one human-readable line, and
  it is `Assessment.verdict`, so a decision can never carry another decision's reason.
- `FileDiscovery.auditable(path, *, must_exist=True)` is stage 0. It asks what `files()` asks:
  the shape rules, then `git check-ignore` inside a checkout. `auditable_paths(paths)` is the
  batch form and makes one git call for the whole batch; `must_exist=False` keeps a deleted path,
  so stage 1 can still remove its nodes.
- `FileDiscovery.auditable_shape(path)` is the shape half alone and never runs git, which is what
  a hook can afford per event. It is the stage 0 seam for S8b's `/events`, where a path the edit
  deleted must still be admitted; nothing calls it before that slice.
- `GraphDB.forget_facts(paths)` drops those files' cached facts in one transaction, which is what
  the `removed` outcome persists with.

## Models

- Exported records: `Finding` and `ManifestEntry` are frozen pydantic models; `ScanResult` and
  `IndexEntry` are mutable aggregates. Their JSON field names match the CLI's output shape, which
  is documented in [report.md](report.md).
- Enums: `Severity`, `VerdictKind`, `FileRole`, `Category`.
- `ScanEngine` is the class `audit_target` drives. `ScanEngine.for_target(path)` builds one with
  the resolved root and config when you need `scan_path`, `scan_file` or `scan_file_indexed`
  directly.
