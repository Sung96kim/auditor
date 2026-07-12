# plugin/skills/write-detector/references/plugin-api.md
# Writing an auditor detector

- Detectors live in `.auditor/plugins/` and are auto-discovered when the repo is scanned.
- Confirm the current API before writing: `auditr plugins list` (loaded detectors + their source)
  and `auditr rules list` (every shipped rule and its metadata). Match a nearby built-in detector's
  shape — the base class, decorators, and registration are defined in the `auditor` package.
- Each rule sets: `rule_id` (unique, `LOCAL-` prefix for repo rules), `category`, `default_severity`
  (`blocking|high|medium|low|suggestion`), and `verdict_kind` (`auto` = the tool decides;
  `candidate` = the agent judges). Emit findings on the precise line (skip directives anchor to it).
- A detector is production code: it needs a test. Put a fixture file with a known violation and assert
  the rule fires on the expected line, and a clean fixture that yields nothing.
- **Trust gate**: `.auditor/plugins/*.py` executes code, so it's untrusted by default. Load it with
  `trust_local_plugins=true` in config, or `-a`/`--allow-local-plugins` on `scan`/`ignore` — `report`
  does not expose that flag. Verify with `auditr scan <fixture> -a -f json`, not `auditr report`.

See `template.py` for a minimal starting point.
