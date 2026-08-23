# plugins reference

`auditr plugins list` shows every detector, language auditor, and reporter loaded for a repo, with
the source recorded for each, plus any warning the loader raised. `auditr plugins list --help`
lists every flag. It loads the repo's config first, so what it prints is what a scan of that repo
would use.

## Common invocations

```bash
# what is loaded for the repo containing the working directory
auditr plugins list

# another checkout
auditr plugins list -r ../other-repo

# raw JSON
auditr plugins list --json
```

## What gets loaded, and from where

- Entry points: a distribution advertising `auditor.detectors`, `auditor.languages`,
  `auditor.reporters`, or `auditor.profiles` is imported whenever it is installed.
- Config-named modules: `plugins = ["acme.rules"]` in the repo's config imports those modules.
- Local files: `.auditor/plugins/*.py` in the repo, sorted by name.
- Entry points and config-named modules load unconditionally. Local files do not; see below.
- A plugin that raises on import does not crash the auditor. The failure becomes a warning in the
  `plugins list` output.
- The output has one section per registry: detectors, languages, reporters. Profiles are TOML
  resolved by name or path rather than registered classes, so they get no section.

## Local plugins and trust

- Importing a local file executes it, so `.auditor/plugins/*.py` are ignored by default and the
  command warns how many files it skipped.
- Two ways to load them: `trust_local_plugins = true` in the repo's config
  ([configuration.md](configuration.md)), or `-a`/`--allow-local-plugins` on the command.
- `scan` and `ignore add` take `-a`. `plugins list` and `report` do not, so for those the config
  field is the only switch.
- Prefix repo-local rule ids with `LOCAL-` so they never collide with a built-in or another
  plugin's id.

## The plugin contract

- A plugin registers by subclassing: `Detector`, `LanguageAuditor`, and `Reporter` auto-register
  the moment the class body executes. There is no separate registration call.
- One `Detector` subclass is one rule id: class-level metadata (category, default severity,
  verdict kind, language, framework, version, standard references) plus a `run(ctx)` that returns
  the findings for one file.
- An intermediate base class that must not register sets `abstract = True`.
- A detector may declare a category string that is not built in; config then accepts it.
- Registration records a source: the module name or file path the loader imported, or `built-in`
  for a rule that ships with the auditor.
- A class one plugin imports from another module is credited to the importing plugin, unless that
  module is a loaded plugin in its own right, in which case it keeps its own name.
- The full metadata table, the `AuditContext` fields a detector may read, and the detector shapes
  with worked examples are in the bundled skill:
  [write-detector](../../plugin/skills/write-detector/SKILL.md) and its
  [plugin API reference](../../plugin/skills/write-detector/references/plugin-api.md). The skill
  ships with the Claude Code plugin ([claude-code-plugin.md](claude-code-plugin.md)).

## Seeing plugin rules

- `plugins list` and `auditr rules list` both load the repo's config and plugins, so either shows
  plugin-contributed detectors; see [rules.md](rules.md) for what each row carries.
- To confirm a local rule actually fires, scan a fixture with `-a`:
  `auditr scan path/to/fixture -a -f json`.
