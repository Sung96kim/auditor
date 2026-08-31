---
name: setup-auditor
description: Onboard a repo to auditor — install the CLI, scaffold .auditor/config.toml, pick a profile, write a baseline, and register the MCP server. Idempotent. Use when setting up or configuring auditor.
---

Set up auditor in this repo. Report what already exists; don't clobber it.

## Steps

1. Check the CLI: `auditr version`. If missing, install with only the extras this repo needs
   (`references/config-guide.md` has the decision table): `uv tool install "auditr[mcp,ts]"`,
   dropping `ts` for a repo with no TypeScript. `mcp` is the common case for any agent-driven
   setup, and the semantic graph needs no extra.
2. Check whether the repo is already configured: `.auditor/config.toml` or a `[tool.auditor]`
   table in `pyproject.toml`. If absent, scaffold `.auditor/config.toml` and choose a profile —
   `references/config-guide.md` has what `base`/`strict`/`pydantic`/`all-strict` each actually
   enable (read straight from the profile TOMLs) and when to pick which. `auditr config show`
   confirms what resolved.
3. Create the user config home: `auditr init --repo`. It writes `$AUDITOR_HOME/config.json` (the
   personal settings, never committed), the generated `config.schema.json` an editor completes
   against, and this repo's overlay plus breadcrumb under `$AUDITOR_HOME/repos/<key>/`. Nothing
   is written into the repository. `auditr init --check` reports unknown keys and a leftover
   `.auditor/.status.json` from an older release; `auditr init --clean-status` deletes that file.
4. Write the repo's own policy. `observer_allowed` is a **top-level** `[tool.auditor]` key, never
   under `graph.*`: `observer_allowed = false` is the repo's hard opt-out from the background
   observer and it wins over any user setting. Leave it out to keep the default (`true`). State
   the minimum `auditr` for the config you just wrote before the team commits it: the graph
   config is `extra="forbid"`, so an older install refuses a key it does not know rather than
   ignoring it, and `auditr version` on this machine is the floor to quote.
5. Write a baseline so pre-existing findings don't gate new work:
   `auditr scan . --write-baseline .auditor/baseline.json`. Skip this on a repo genuinely starting
   from zero findings — see `references/config-guide.md` for when a baseline is/isn't worth it.
6. Confirm the MCP server. Via this plugin, `plugin/.mcp.json` registers it automatically, so no
   action is needed. Outside the plugin, `references/config-guide.md` has the `claude mcp add` /
   Codex registration commands.
7. Summarize: CLI version + extras installed, profile chosen, the repo policy written, whether the
   user config home was created, whether a baseline was written (and how many findings it
   snapshotted), MCP status, and ask the user to run `auditr graph eval` once. It measures what a
   runner gets right on this repo and is the number the activation tiers are set from, so the
   graph's refinement layer should not be trusted before it has run.

## References

- `references/config-guide.md` — the full `.auditor/config.toml` shape (every top-level key,
  what it controls); what each profile (`base`/`strict`/`pydantic`/`all-strict`) actually turns
  on, read from the real profile TOMLs; the baseline workflow verified end-to-end (write → scope
  to new findings → gate); the install-extras decision table; MCP registration for the plugin,
  bare CLI, and Codex; a fully worked fresh-repo onboarding walkthrough.
