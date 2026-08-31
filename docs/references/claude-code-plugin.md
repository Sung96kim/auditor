# Claude Code plugin reference

auditor ships as a Claude Code plugin that drives the `auditr` CLI: skills, a review subagent,
hooks, a status line and the MCP server. The plugin itself carries no Python dependencies, so
install the CLI separately. Each piece is configured in its own file under `plugin/`: skills in
`plugin/skills/<name>/SKILL.md`, the subagent in `plugin/agents/auditor-reviewer.md`, hooks in
`plugin/hooks/hooks.json`, the status line in `plugin/settings.json`, the MCP server in
`plugin/.mcp.json`, and the manifest in `plugin/.claude-plugin/plugin.json`, which points at the
skills, the subagent, and the MCP server; hooks and the status line are discovered by convention.

## Common invocations

```bash
# register the marketplace
claude plugin marketplace add Sung96kim/auditor

# enable the plugin in a session
/plugin install auditor

# run against a local checkout instead of the marketplace
claude --plugin-dir ./plugin

# invoke a skill by name
/auditor:judge-findings

# dispatch the review subagent
@auditor-reviewer
```

## Install

- `claude plugin marketplace add Sung96kim/auditor` registers the marketplace defined by the
  repo-root `.claude-plugin/marketplace.json`, which points at `./plugin`.
- `/plugin install auditor` enables it in a session.
- `claude --plugin-dir ./plugin` loads the checkout directly, for plugin development.
- The CLI is a separate install (`uv tool install auditr`); the hooks and status line stay silent
  when `auditr` is not on PATH.

## Skills

Invoked as `/auditor:<name>`, and auto-invoked when the task matches the skill's description. Each
`SKILL.md` is a thin workflow and carries deeper `references/` files the agent loads on demand.

- `judge-findings`: run auditor and judge its candidate findings, deciding fix, skip directive, or
  dismiss for each.
- `audit-changes`: audit only what changed against a base ref and gate it, for PR and CI review.
- `setup-auditor`: onboard a repo, covering install, config scaffold, profile choice, baseline and
  MCP registration.
- `explore-graph`: query the semantic code graph for dead code, call and dependency impact, symbol
  usages and clusters.
- `malware-scan`: run the opt-in supply-chain pass (ClamAV plus osv-scanner) and triage the
  findings.
- `aggregate-report`: produce a repo-wide `AUDIT.md` rollup from the incremental index.
- `write-detector`: author a repo-local detector under `.auditor/plugins/`, with a required test.

## Subagent

- `auditor-reviewer` runs a full or changeset scan in its own context and returns a triaged report:
  severity rollup, worst findings per file, and judged `candidate` verdicts.
- Use it for deep audits that would otherwise flood the main conversation. Dispatched directly with
  `@auditor-reviewer`, it runs in the background by default.
- It prefers the MCP tools when connected and falls back to the `auditr` CLI over Bash. Its tool
  grant is `Read`, `Grep`, `Glob`, `Bash` and `mcp__auditor__*`, and it inherits the session's
  model.
- The `judge-findings` skill dispatches this agent rather than judging in the main context.

## Hooks

`plugin/hooks/hooks.json` registers four stdlib-only scripts. Each no-ops when `auditr` is missing
from PATH or the event payload is unusable. The environment variables that tune them are in
[configuration.md](configuration.md).

- `session_start.py` on `SessionStart`: reports whether auditor is installed and whether this repo
  is configured (`.auditor/config.toml`, or a `[tool.auditor]` table in `pyproject.toml`).
- `audit_edit.py` on `PostToolUse` matching `Edit|Write`: runs `auditr report` on the file just
  changed and feeds the findings back in-turn, or detaches an incremental repo scan instead. It
  only considers `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.sh` and `.bash` files, and rolls findings
  below its severity floor into a one-line count while `blocking` findings always surface.
- `verify_stop.py` on `Stop`: a verify-before-stop gate, off by default. It scans the uncommitted
  delta (`--since HEAD`) and blocks finishing while the gate still trips. A tool or config error
  surfaces a note and does not block, so a hiccup cannot wedge the agent.
- `session_end.py` on `SessionEnd`: detaches this session from the observer daemon. It reads no
  reason field and emits nothing; a `SessionEnd` hook's output is discarded anyway.

Every one of the four also hands its payload to `auditr-observer hook <event> --client claude-code`
on that command's stdin, before its own audit behaviour runs and independently of the environment
variable that gates it: `AUDITOR_AUTOHOOK` and `AUDITOR_VERIFY_HOOK` turn the audit halves off and
`AUDITOR_OBSERVER=0` turns the observer half off, and it does so before any process is started,
so switching it off costs nothing per event. `auditr-observer` is resolved on PATH and nowhere
else: there is no `uvx` fallback, because resolving a package inside a hook's one to three second
budget cannot finish, and `session_start.py` says once on stderr when the client is not installed.
The observer half holds no HTTP client, no port lookup and no spool of its own: those live once,
in `auditr_observer.py` ([observer.md](observer.md)). Measured cost of the delegation: about 42 ms
per hook, against the 200 ms budget the observer design gives it.

## Status line

- Configured in `plugin/settings.json`, which also turns on `subagentStatusLine`.
- It walks up from the session's cwd for `.git`, `pyproject.toml` or `.auditor`, hashes that
  repo's git common dir the same way `auditr` does, and reads
  `$AUDITOR_HOME/repos/<repo_dir_key>/status.json`. `git rev-parse` is the only subprocess it
  runs, twice outside a git checkout for the pre-2.31 fallback, and the database is never opened.
  Which runs write that file is in [scan.md](scan.md).
- It reads the file's `scan` block. An older in-repo `.auditor/.status.json` is ignored.
- The Stop hook's `scan --since HEAD` does not write that block, so the segment keeps showing the
  last full scan of the repo rather than the uncommitted delta. See [scan.md](scan.md).

```
● auditor  2 blocking  5 high  +17 lower
```

- The dot color follows the worst severity present. With nothing open it shows `auditor  clean`;
  with no cache, or a corrupt one, `auditor  not set up`.
- A `⟳` marker appears once the cache is more than 15 minutes old.

## Bundled MCP server

- `plugin/.mcp.json` registers the server as `uvx --python 3.13 --from "auditr[mcp]" auditr-mcp`,
  so enabling the plugin registers it with no separate `claude mcp add`.
- `uvx` fetches the `mcp` extra on demand, so this path works even when the installed CLI lacks
  that extra.
- Everything about the tools themselves is in [auditr-mcp.md](auditr-mcp.md).
