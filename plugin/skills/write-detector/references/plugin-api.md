# plugin/skills/write-detector/references/plugin-api.md
# The detector API

Source of truth: `auditor/languages/base.py` (`Detector`, `AuditContext`), `auditor/models.py`
(`Category`, `Severity`, `VerdictKind`), `auditor/plugins.py` (loading). Confirm against the
installed version before relying on any of this: `auditr plugins list`, `auditr rules list`.

## The contract

A detector is a `Detector` subclass. `__init_subclass__` auto-registers it the moment the class
body executes — importing the module is enough, there's no separate "register" call:

```python
class MyRule(Detector):
    rule_id: ClassVar[str] = "LOCAL-..."
    category: ClassVar[Category | str] = Category.CORRECTNESS
    default_severity: ClassVar[Severity] = Severity.HIGH
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.AUTO   # default if omitted

    def run(self, ctx: AuditContext) -> list[Finding]:
        ...
```

One `Detector` subclass = one `rule_id` = one row in `auditr rules list`. `run` returns every
finding for `ctx`'s file; return `[]` when the file is clean or doesn't apply.

## Class-level metadata (`ClassVar`s on `Detector`)

| Field | Type | Meaning |
|---|---|---|
| `rule_id` | `str` | Unique id. Use a `LOCAL-` prefix for repo rules so they never collide with a built-in or another plugin's id. |
| `category` | `Category \| str` | One of the built-in `Category` values (below), or a new string — a plugin may mint its own category (`tests/fixtures/data/plugins/house_rules.py` uses `"house"`). |
| `default_severity` | `Severity` | `blocking \| high \| medium \| low \| suggestion` — see `references/patterns.md` for how to pick one. |
| `verdict_kind` | `VerdictKind` | `auto` (default) or `candidate` — see `references/patterns.md` for the decision. |
| `language` | `str` | Defaults to `"python"`. Only change it if you're also authoring a `LanguageAuditor` for a new language. |
| `framework` | `str \| None` | The framework this rule is specific to (e.g. `"sqlalchemy"`, `"pytest"`); `None` = framework-agnostic. Purely descriptive/filterable (`auditr rules list --framework ...`) — nothing gates on it automatically; a framework-specific rule still has to check for the framework itself (see the framework-gated shape in `patterns.md`). |
| `version` | `str` | Defaults to `"1"`. Bump it when you change a rule's *logic* (not its config) — the cache fingerprints on `(detector version, resolved config)`, so a version bump invalidates stale cached findings for that rule. |
| `checklist_item` | `int \| None` | Only meaningful for this project's own internal checklist; leave `None` in a plugin. |
| `standard_refs` | `tuple[str, ...]` | External standard citations, e.g. `("bandit:B602", "owasp:A03")`. Empty tuple if none. |
| `abstract` | `bool` | `True` on an intermediate base class that itself must not register (e.g. a shared `_MyFrameworkRule(Detector)` with `abstract: ClassVar[bool] = True` that concrete rules subclass). Leave `False` (default) on a concrete rule. |
| `repo_level` | `bool` | `True` marks a rule as computed by a dedicated repo-level pass instead of per-file `run`. **Don't set this in a plugin** — `repo_level` detectors are skipped entirely at per-file collection time (`auditor/engine.py`, `_partition_rules`); there is no plugin hook into the built-in cross-file passes (dedup, dead-code, graph). A plugin detector's `run` only ever sees one file at a time — see the cross-file-resolving shape in `patterns.md` for how far you *can* reach beyond the current file. |

## `Category` — built-in values

`security`, `malware`, `supply-chain`, `secrets`, `correctness`, `typing`, `async`, `config`,
`oop-composition`, `style`, `react`, `a11y`, `design-system`, `testing`, `dead-code`. Config
validates rule/category references against this set plus any plugin-registered category strings
— pass a new string as `category` and it's just added to the known set, no extra registration
step.

## `AuditContext` — what `run(self, ctx)` receives

One instance per file, built once per scan (`auditor/languages/base.py`). Everything a detector
needs to make its call lives here — a detector should never re-read or re-parse the file itself:

| Field | Type | What it's for |
|---|---|---|
| `ctx.file_path` | `str` | Repo-relative path. |
| `ctx.source` | `str` | Full file text. |
| `ctx.lines` | `list[str]` | `source.splitlines()`, 1-indexed via `ctx.line_text(n)`. |
| `ctx.tree` | `ast.Module` | The parsed AST — `ast.walk(ctx.tree)` is the usual entry point. |
| `ctx.role` | `FileRole` | `production \| test \| test_support \| script \| generated`. Config (not the detector) applies role-relaxed severity/verdict — a detector doesn't need to branch on role itself. |
| `ctx.config` | `ResolvedConfig` | The per-file resolved settings. `ctx.config.settings` is the raw `AuditorSettings` (e.g. `ctx.config.settings.sqlalchemy.async_session` for a project-declared fact — see the config-gated shape in `patterns.md`). |
| `ctx.package_root` | `str \| None` | The importable package root containing this file, if any. |
| `ctx.project_deps` | `frozenset[str]` | Top-level dependency names the scanned project declares (e.g. `"pydantic" in ctx.project_deps`) — gates a rule to projects that actually use the framework. |
| `ctx.sibling_modules` | `tuple[str, ...]` | Names of modules alongside this one in the same package. |
| `ctx.defines_basesettings` | `bool` | Whether this file defines a `pydantic_settings.BaseSettings` subclass. |
| `ctx.resolver` | `CalleeResolver \| None` | Resolves a call in this file to its callee's AST **in another file** — the mechanism behind the cross-file-resolving shape. `None` when cross-file resolution wasn't set up for this scan (e.g. `--isolated`); always null-check before using it. |

`self.make_finding(ctx, *, line, message, suggestion=None, evidence=None)` builds the `Finding`
from the class-level metadata plus these call-specific fields — `evidence` defaults to
`ctx.line_text(line)` if you don't pass one explicitly. Always pass the precise offending line:
skip directives (`# auditor: skip: <RULE-ID>`) anchor to `Finding.line` exactly, not the
statement's span.

## Loading a detector — three mechanisms

From `auditor/plugins.py`, in load order:

1. **Entry points** — a distributed package registers under one of the
   `auditor.detectors` / `auditor.languages` / `auditor.reporters` / `auditor.profiles` groups.
   Loads unconditionally, no trust gate (it's an installed package, already vetted by installing
   it at all).
2. **Config-named modules** — `[tool.auditor] plugins = ["acme.rules"]` in `pyproject.toml` or
   `.auditor/config.toml`; `importlib.import_module("acme.rules")` on load. Also unconditional —
   it's a module the repo's config explicitly names, same trust level as any other dependency.
3. **Local repo plugins** — every `.auditor/plugins/*.py` file, sorted, `exec`'d as a standalone
   module. **Gated**: only loaded when `trust_local_plugins = true` in config or `-a`/
   `--allow-local-plugins` is passed on `scan`/`ignore` (not `report`). Ungated by default because
   this is exactly the kind of file that ships in a checkout and runs arbitrary code the moment
   the repo is scanned.

A broken plugin (bad syntax, raises on import) never crashes the auditor — it's caught and
recorded as a warning (`auditr plugins list` surfaces it), and the rest of the scan proceeds.

Config loading is **two-phase**: plugins load first, *then* config validates — so
`.auditor/config.toml` can reference a plugin-contributed `rule_id` (e.g. to override its
severity) without a chicken-and-egg failure.

## See also

- `template.py` (this skill dir) — a minimal working AST-walk detector, verified to load and fire.
- `references/patterns.md` — the three detector shapes, the severity/verdict decision, and how to
  test a detector end-to-end.
