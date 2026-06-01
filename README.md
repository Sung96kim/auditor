<p align="center">
  <img src="assets/icon.svg" width="120" alt="auditor logo">
</p>

<h1 align="center">auditor</h1>

<p align="center"><em>A token-efficient repo auditor for coding agents (Claude Code, Codex, …) and CI.</em></p>

A **token-efficient repo auditor** for coding agents (Claude Code, Codex, …) and CI.

It does the mechanical, deterministic part of a code audit — parsing, building the
class/function manifest, running ~48 anti-pattern detectors, hashing for an incremental
cache — so an agent spends tokens only on the genuine judgment calls. Findings are split
into `auto` (the tool decided) and `candidate` (evidence only; you judge).

Think of it as "like an MCP, but also a CLI": run it over `bash` in any harness, or expose
it as an MCP server. Works on any repo, directory, or single file.

## Why

The checklist-style audit skills make the **agent** read every file, run ~15 greps, and
hand-transcribe a manifest on every pass — expensive, and re-auditing re-pays the full cost
even for unchanged files. `auditor` moves all of that into deterministic Python:

- **Manifest + detectors** run in-process from a single AST parse.
- A **SQLite index** caches findings per `(file, rule)`; re-auditing 3 of 358 files
  re-parses only those 3. Editing one rule's threshold re-runs only that rule.
- The agent reads the compact JSON, then looks at only the flagged sites.

## Install

**Recommended — install the CLI globally as a tool** (so `auditor` is on your PATH in any repo):

```bash
uv tool install .                  # from a checkout
uv tool install git+https://github.com/Sung96kim/auditor   # from GitHub
uv tool install ".[mcp]"           # include the FastMCP server (auditor-mcp)
uv tool install ".[ts]"            # include TypeScript/React support (tree-sitter)
```

For development on the auditor itself:

```bash
uv sync                 # core
uv sync --extra mcp     # + FastMCP server
uv sync --extra dev     # + pytest/ruff
```

## CLI

```bash
auditor scan .                       # audit the repo (JSON to stdout)
auditor scan . -i                    # --incremental: use/update the cache (.auditor/index.db)
auditor scan . -f sarif              # --format: json | sarif | md | html
auditor scan . -f html -o audit.html # --output: write the report to a file instead of stdout
auditor scan . --serve               # render HTML and open it in a browser on a local port
auditor scan . -p strict             # --profile: run any repo at strict strength (no config edits)
auditor scan . -x '**/migrations/**' # --exclude: ad-hoc ignore glob (repeatable), on top of config
auditor scan tests/ -t               # --strict-tests: audit test code at full production strength
auditor report path/to/file.py       # single file, stateless (manifest + findings)
auditor manifest path/to/file.py     # AST manifest only (no detectors)
auditor discover .                   # list auditable files with their classified role
auditor aggregate . -o AUDIT.md      # roll the index up into AUDIT.md
auditor rules list --category security --standard bandit
auditor config show                  # the resolved configuration
auditor plugins list                 # loaded detectors/languages/reporters + their source
```

## Standards & configuration

Ships recognized **industry-standard rulesets** as the baseline and lets each repo tailor
them — the ruff/eslint model. Config lives in `[tool.auditor]` in `pyproject.toml` **or** a
standalone `.auditor/config.toml` (standalone wins on conflict).

```toml
[tool.auditor]
extends = "strict"                 # base | strict | pydantic | all-strict | a path
exclude = ["migrations/**"]

[tool.auditor.rules]
PY-TYPING-MISSING-HINTS = { severity = "high" }
PY-OOP-CONSTRUCTOR-WALL = { enabled = true, threshold = { wall_kwarg_min = 10 } }

[tool.auditor.categories]
security = { min_severity = "high" }
```

- **Profiles**: `base` (industry floor: security/correctness/async/typing/config + cross-file
  dedup on; opinionated OOP/composition off), `strict` (adds OOP/composition + complexity),
  `pydantic`, `all-strict` (audits every role — tests included — at production strength).
- **Roles**: every file is classified `production | test | test_support | script | generated`
  from path + content. Test code is audited under a **relaxed** policy (assert-for-auth,
  hardcoded-secret, etc. are noise-by-design in tests) — flip it to full strength with
  `--strict-tests` or `test_mode = "strict"`.
- **Per-rule cache**: each rule has a fingerprint = `hash(detector version + its resolved
  config)`; cached findings are reused only when the file content hash **and** that rule's
  fingerprint match.

## Detectors

~48 Python rules across `security` (Bandit/OWASP-mapped), `correctness`, `typing`, `async`,
`config`, `oop-composition`, and `style`, plus cross-file duplicate-model/function dedup.
Each carries a stable `rule_id`, a category, a default severity, and (for security)
`standard_refs` like `bandit:B602` / `owasp:A03`. `auditor rules list` enumerates them.

**TypeScript / React** (`.ts/.tsx/.js/.jsx`, via the `ts` extra — tree-sitter): objective,
**framework-agnostic** rules only — accessibility (`a11y`: non-interactive `onClick`,
icon-only button without a label, `<img>` without alt, positive `tabIndex`), structure
(`react`: multiple components per file, repeated sibling JSX → `.map()`, duplicate imports),
and cross-file **DRY/dedup** (`TS-XFILE-DUP-COMPONENT`/`DUP-FUNCTION` — same normalized
component/function shape across files → extract a shared one). The auditor deliberately does
**not** encode a design system: it never says "this should be `<Badge>`" or "use the size
prop" — that needs the project's primitive vocabulary, which is the agent + design-system
skill's judgment layer. The auditor surfaces the structural fact; you map it to your code.

## Plugins

Extend by subclassing — the ABCs (`Detector`, `LanguageAuditor`, `Reporter`) auto-register.
Three discovery mechanisms: **entry points** (`auditor.detectors`, …), **config-named
modules** (`[tool.auditor] plugins = ["acme.rules"]`), and local **`.auditor/plugins/*.py`**
(gated behind `trust_local_plugins`/`--allow-local-plugins` — importing them runs code).

## MCP server

```bash
auditor-mcp                       # stdio server (requires the `mcp` extra)
python -m auditor.mcp_server
```

Tools: `scan`, `report`, `manifest`, `discover`, `aggregate`, `rules_list`.

## Programmatic API

```python
from pathlib import Path
from auditor import audit_target, render

results = await audit_target(Path("src"), incremental=True)
print(render(results, "sarif"))
```

## Docker

```bash
docker compose run --rm auditor scan .                 # mounts CWD at /auditor
TARGET=/path/to/repo docker compose run --rm auditor scan . --format sarif
```

## Development

```bash
uv run pytest            # 170 tests
uv run pytest --cov=auditor
uv run ruff check auditor tests
```

The package is held to its own standard: registries and analyzers are classes, config is
typed `pydantic-settings`, the index is async (in-house worker thread, no third-party
driver), and `auditor scan auditor/` on its own source is clean apart from a few
intentional, explainable findings (worker-thread `BaseException` propagation, plugin-load
isolation).
