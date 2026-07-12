---
name: setup-auditor
description: Onboard a repo to auditor — install the CLI, scaffold .auditor/config.toml, pick a profile, write a baseline, and register the MCP server. Idempotent. Use when setting up or configuring auditor.
---

Set up auditor in this repo. Report what already exists; don't clobber it.

## Steps

1. Check the CLI: `auditr version`. If missing, install with the extras the repo needs:
   `uv tool install "auditr[mcp,ts,graph]"` (drop extras that don't apply).
2. Scaffold config if absent: create `.auditor/config.toml` (see `auditr config show` for the
   resolved defaults). Choose a profile with `-p` (base | strict | pydantic | all-strict).
3. Write a baseline so pre-existing findings don't gate new work:
   `auditr scan . --write-baseline .auditor/baseline.json`.
4. Confirm the MCP server: it ships with this plugin (`auditr-mcp` via uvx). For a non-plugin
   setup, register it with `claude mcp add`.
5. Summarize: CLI version, profile, whether a baseline was written, MCP status.
