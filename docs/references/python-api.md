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

## Models

- Exported records: `Finding` and `ManifestEntry` are frozen pydantic models; `ScanResult` and
  `IndexEntry` are mutable aggregates. Their JSON field names match the CLI's output shape, which
  is documented in [report.md](report.md).
- Enums: `Severity`, `VerdictKind`, `FileRole`, `Category`.
- `ScanEngine` is the class `audit_target` drives. `ScanEngine.for_target(path)` builds one with
  the resolved root and config when you need `scan_path`, `scan_file` or `scan_file_indexed`
  directly.
