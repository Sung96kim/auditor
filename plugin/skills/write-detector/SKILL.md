---
name: write-detector
description: Author a new repo-local auditor detector under .auditor/plugins/, with a required test. Use when adding a custom lint/anti-pattern rule to auditor.
paths: "**/*.py"
---

Add a repo-local detector to auditor.

## Steps

1. Ground yourself in the current API: `auditr plugins list` and `auditr rules list`. Read a nearby
   built-in detector in the `auditor` package to copy its exact base class + registration.
2. Pick a shape (`references/patterns.md`): AST-walk (default — copy `template.py`),
   framework/config-gated, or cross-file-resolving (`ctx.resolver`). Copy `template.py` (in this
   skill dir) into `.auditor/plugins/<name>.py` and adapt: set `rule_id` (`LOCAL-` prefix),
   `category`, `default_severity`, `verdict_kind`, and the check logic.
3. Decide `default_severity` and `verdict_kind` deliberately, not by default — `references/
   patterns.md` has the calibration table and the auto-vs-candidate decision rule with real
   examples of each. Getting `verdict_kind` wrong either blocks CI on something that needed a
   judgment call, or lets a deterministic bug quietly sit in `candidate` limbo forever.
4. Write the **required** test (new production code must have tests): a fixture with a known
   violation asserting the rule fires on the right line, plus a clean fixture that yields nothing.
   `references/patterns.md` has a verified, copy-pasteable pattern — including the
   module-registration footgun (re-importing the plugin module twice raises `duplicate rule_id`).
5. Local plugins are untrusted by default — set `trust_local_plugins=true` in config, or pass
   `-a`/`--allow-local-plugins` to `scan`/`ignore` (`report` doesn't have the flag).
6. Verify: `auditr scan <fixture> -a -f json` flags it (not `auditr report`, which lacks `-a`).
   `references/patterns.md` has a fully worked run of this exact sequence.

## References

- `references/plugin-api.md` — the full `Detector` metadata contract (every `ClassVar`, what it
  does), the `Category` list, every `AuditContext` field `run()` can use, and how the three
  plugin-loading mechanisms (entry points, config-named modules, local `.auditor/plugins/`)
  differ.
- `references/patterns.md` — the three detector shapes with real, grounded examples (AST-walk,
  framework/config-gated, cross-file-resolving via `ctx.resolver`); how to pick
  `default_severity` and decide `auto` vs `candidate` for `verdict_kind`; a verified test pattern;
  a fully worked end-to-end (write rule → test → `auditr scan <fixture> -a`).
