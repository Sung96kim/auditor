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

- `auditor.__all__` is the supported surface: `audit_target`, `render`, `load_config`,
  `ScanEngine`, `IndexStore`, `AuditorSettings`, `ResolvedConfig`, and the records and enums below.
  Anything else is internal and may move between releases.

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
- `ScanEngine` is the class it drives. `ScanEngine.for_target(path)` builds one with the resolved
  root and config when you need `scan_path`, `scan_file` or `scan_file_indexed` directly.

## `render`

- `render(results: list[ScanResult], fmt: str) -> str` renders a result list through the reporter
  registered for `fmt`.
- Built-in formats: `json`, `sarif`, `md`, `html`. A plugin can register more
  ([plugins.md](plugins.md)).
- An unknown `fmt` raises `ValueError` naming the available formats.

## `load_config`

- `load_config(root: Path, *, profile=None, allow_local_plugins=False, loader=None,
  overrides=None) -> AuditorSettings` returns the merged repo configuration. It is the only loader;
  there is no separate report call.
- It loads plugins between the raw read and validation, so a config may name plugin-contributed
  rules. `profile` replaces the repo's `extends` for this load.
- `settings.unknown_keys` is the tuple of dotted paths no model declares, filled at load time.
  Unknown keys never fail the load and the loader never warns; the CLI and the MCP server print
  them once on stderr. It is excluded from every dump, so `model_dump()` carries the configuration
  only.
- `AuditorSettings.merged(raw)` pairs a validated model with the unknown keys from the same raw
  dict, for a caller that merged the layers itself.
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
- Per-table stores hang off the handle. Repo-scoped: `repos`, `ignores`, `files`, `findings`,
  `shapes`, `graph`. Identity-scoped: `runs`, `refinements`, `tuning`, `evals`.
- `await index.transaction(fn)` runs `fn(conn)` on the live connection as one commit and rolls back
  on any exception. It is what a graph build uses to land nodes, edges, the queue and the findings
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

The objects behind `auditr graph refine` and `auditr graph refinements`. They are not exported from
`auditor`; import them from `auditor.graph.refine`.

- `service.RefinementService(index, root, settings, user, registry=None)`: `begin`, `propose`,
  `brief`, `preview`, `build_brief`, `status`, `commit`, `abort`, `terminate`, `prune`, `rebuild`.
  The only supported way to record a graph correction, and what the `graph_refine_*` MCP tools
  call. `brief` renders an open run's brief and records it on the row the first time it is read;
  `preview` renders the same brief for a scope without opening a run. `commit`, `abort` and
  `terminate` each take an optional `RunAttribution` (the cost, the tool trace, the SDK session and
  the producer's one-line summary). `registry` defaults to the registry this process shares for
  that repo identity; passing a fresh one splits the staging.
- `service.RefinementLedger(index=...)`: `accept`, `revert`, `pin`, `prune`, `refinement`. The
  by-hand half, over an index handle alone, with no checkout root and no run, which is what
  `auditr graph refinements accept <id>` has to work without.
- `verify.FactVerifier` is the AST-fact check one proposal has to pass. It is pure, so a caller
  reads the files and hands the facts in; `facts.FactReader` is the reader that does that reading
  and `facts.BriefBuilder` turns those reads into the `Brief` a run works from.
  `RefinementService(..., facts=reader)` injects one, which is how an eval masks an edge without
  the stored queue filling the gap.
- `runner.RefinementRunner` is the producer ABC: `run(job)` opens a run, records its brief, works,
  and closes it, answering with a `RunProduct`. How the run ended is on the stored row, not on the
  product, so the two cannot disagree. `runner.FakeRunner` replays a scripted set of proposals,
  which is how the whole path is tested with no SDK installed.
- `sdk_runner.SdkRunner` is the Claude producer, SDK-free by design: it talks through an injected
  factory answering to `ClientSession`. `sdk_client.claude_client(options, tools)` is the only
  importer of `claude_agent_sdk`.
- `drive` (`select_runner`, `build_runner`, `refine`, `brief`, `evaluate`) is the one module the
  CLI and the MCP tools import from the runner half, so neither can drift on the runner choice or
  on the payload. It owns the single `observer-claude` import guard.
- `eval` is what `auditr graph eval` measures: `Population.of(facts)` reads the graph and keeps the
  edges tier B is measured on, `population.sample(...)` draws trials per stratum, `EvalSuiteSpec.of`
  is one suite's draw and verdict rule, `Judge.over(trials)` scores a batch without storing
  anything, and `EvalRun(...).report(suites)` writes one `graph_evals` row per completely measured
  stratum (`dry_run=True` answers with the plan and opens no run).
- `models.wilson_lower(correct, total, z=1.96)` and `models.flawless_floor(min_precision, z=1.96)`
  are the Wilson lower bound a tier gate reads and the smallest flawless run that clears a given
  bar. `flawless_floor` searches to 10,000 trials and answers `None` beyond it, which every caller
  reads as "unprovable at this bar". The shipped bar is in [configuration.md](configuration.md).
- `auditor.graph.query.LogQuery(index)`: `page(spec)` for `graph log`'s two views and
  `refinements(statuses, limit)` for the corrections page. The one reader the CLI and the MCP tools
  both call, so the two surfaces cannot drift on ordering or on what a time window means.

## Change assessment

`auditor.observer` holds the gate that decides whether an edit batch is worth a refinement run.
Every function in it is pure: no store handle, no filesystem read, no clock, so the caller does the
I/O and hands the values in. No shipped command calls it; it is here for an embedder building one.

- `assess.assess_path(edited) -> PathVerdict` classifies one edited path against the facts cached
  for it, and `assess.stage_one(edited) -> Stage1` does a whole batch, deduplicating by path.
- `assess.assess(stage1, *, before, after, scheduling, budget, max_nodes_per_run, flow_nodes=...)`
  is the whole thing for a batch that was rebuilt for; `assess_unchanged(stage1)` is the answer for
  one that was dropped.
- `assess.decide(*, new_pairs, bounded_pairs, stale_refinements, scheduling, budget, kind=...)`
  is the rule itself, public because edit, suspect and verify batches gate against the same state
  and `kind` is what tells them apart. It returns the decision and the pairs a run would take.
- `assess.new_rows`, `new_pairs`, `resolved_pairs` and `staled_refinements` are the queue diffs.
  They compare on `graph_unresolved`'s whole key, `(node_id, name, reason)`, and report distinct
  `(node_id, name)` pairs, so a name asked twice for two reasons is two rows to diff and one
  question to count.
- `budget.budget_state(spend, *, config, priced=True, evaluated=False)` builds the `BudgetState`
  `decide` reads; `budget.window_start(now)` is a rolling 24 hours rather than a calendar day.
- The vocabulary the store shares lives in `auditor.graph.refine.models`: `Assessment`,
  `AssessmentDecision`, `BatchKind`, `Decision`, `NodePair` and `Spend`.

## Models

- Exported records: `Finding` and `ManifestEntry` are frozen pydantic models; `ScanResult` and
  `IndexEntry` are mutable aggregates. Their JSON field names match the CLI's output shape, which
  is documented in [report.md](report.md).
- Enums: `Severity`, `VerdictKind`, `FileRole`, `Category`.
