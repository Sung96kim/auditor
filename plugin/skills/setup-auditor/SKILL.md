---
name: setup-auditor
description: Onboard a repo to auditor — install the CLI, scaffold .auditor/config.toml, pick a profile, write a baseline, and register the MCP server. Idempotent. Use when setting up or configuring auditor.
---

Set up auditor in this repo. Report what already exists; don't clobber it.

## Steps

1. Check the CLI: `auditr version`. If missing, install with only the extras this repo needs
   (`references/config-guide.md` has the decision table): `uv tool install "auditr[mcp,ts,graph]"`,
   dropping whichever of `ts`/`graph` don't apply — `mcp` is the common case for any
   agent-driven setup.
2. Check whether the repo is already configured: `.auditor/config.toml` or a `[tool.auditor]`
   table in `pyproject.toml`. If absent, scaffold `.auditor/config.toml` and choose a profile —
   `references/config-guide.md` has what `base`/`strict`/`pydantic`/`all-strict` each actually
   enable (read straight from the profile TOMLs) and when to pick which. `auditr config show`
   confirms what resolved.
3. Write a baseline so pre-existing findings don't gate new work:
   `auditr scan . --write-baseline .auditor/baseline.json`. Skip this on a repo genuinely starting
   from zero findings — see `references/config-guide.md` for when a baseline is/isn't worth it.
4. Confirm the MCP server. Via this plugin, `plugin/.mcp.json` registers it automatically — no
   action needed. Outside the plugin, `references/config-guide.md` has the `claude mcp add` /
   Codex registration commands.
5. Summarize: CLI version + extras installed, profile chosen, whether a baseline was written
   (and how many findings it snapshotted), MCP status.

## References

- `references/config-guide.md` — the full `.auditor/config.toml` shape (every top-level key,
  what it controls); what each profile (`base`/`strict`/`pydantic`/`all-strict`) actually turns
  on, read from the real profile TOMLs; the baseline workflow verified end-to-end (write → scope
  to new findings → gate); the install-extras decision table; MCP registration for the plugin,
  bare CLI, and Codex; a fully worked fresh-repo onboarding walkthrough.
