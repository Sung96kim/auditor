<p align="center">
  <img src="assets/icon.svg" width="120" alt="auditor logo">
</p>

<h1 align="center">auditor</h1>

<p align="center"><em>A token-efficient repo auditor for coding agents (Claude Code, Codex, …) and CI.</em></p>

It does the mechanical, deterministic part of a code audit — parsing, building the
class/function manifest, running **123 anti-pattern detectors** across Python, TypeScript/React,
shell, and package manifests, hashing for an incremental cache — so an agent spends tokens only
on the genuine judgment calls. Findings are split into `auto` (the tool decided) and `candidate`
(evidence only; you judge).

Think of it as "like an MCP, but also a CLI": run it over `bash` in any harness, or expose
it as an MCP server. Works on any repo, directory, or single file — and slots into a PR/CI
loop with `--since main` (audit only what changed) and `--fail-on high` (gate the build).

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

By default `scan` prints a **concise human summary** (severity counts + worst files); an
agent/CI asks for machine output explicitly with `-f` or `-o`.

```bash
auditor scan .                       # readable summary (severity counts, worst files)
auditor scan . -f json               # machine output — json | sarif | md | html
auditor scan . -f html -o audit.html # --output: write the report to a file instead of stdout
auditor scan . --serve               # render HTML and open it in a browser on a local port
auditor scan . -i                    # --incremental: use/update the cache (.auditor/index.db)
auditor scan . -p strict             # --profile: run any repo at strict strength (no config edits)
auditor scan . -x '**/migrations/**' # --exclude: ad-hoc ignore glob (repeatable), on top of config
auditor scan tests/ -t               # --strict-tests: audit test code at full production strength
auditor scan . -vvv                  # -v/-vv/-vvv: log progress to stderr (files / detail / per-finding)
auditor report path/to/file.py       # single file, stateless (manifest + findings)
auditor manifest path/to/file.py     # AST manifest only (no detectors)
auditor discover .                   # list auditable files with their classified role
auditor aggregate . -o AUDIT.md      # roll the index up into AUDIT.md
auditor rules list --category security --standard bandit
auditor config show                  # the resolved configuration
auditor plugins list                 # loaded detectors/languages/reporters + their source
```

### Scope the output

```bash
auditor scan . -s high -s blocking   # --severity: only these levels (repeatable, exact)
auditor scan . -m high               # --min-severity: this level and worse
```

### PR / CI loop

`--since`/`--changed`/`--vs-base` **scope the reported findings to the files you changed** —
but the whole repo is still scanned (cheaply, through the cache) so cross-file/repo-global
rules stay correct, and each changed file is audited **in full** (never just the diff hunks).

```bash
auditor scan --changed                       # files changed in your working tree (vs HEAD)
auditor scan --since main -f json            # files changed vs a ref (branch/origin-branch/SHA/tag)
auditor scan --vs-base                       # vs your base branch (auto-detects main/master/develop)
auditor scan --since main --fail-on high     # CI gate: exit non-zero if any finding is high+
```

`--fail-on <severity>` makes `scan` exit non-zero when any finding is at or above that level.
The gate counts only **confirmed (`auto`) findings** — never `candidate`s, which are for the agent
to judge, not to auto-break CI — and is independent of any display filter. Only local git is run (`diff`/`ls-files`),
so it's identical for ssh and https remotes; an unfetched ref gives a clean "fetch it first"
error. `--vs-base` auto-detects the base branch (first of `main`/`master`/`develop`/
`development`, local or `origin/`); pin it with `[tool.auditor] diff_base = "origin/main"`.

### Baseline (adopt on a legacy repo)

Accept today's findings, then gate only on what you *add* — so a large existing repo can turn
the auditor on without drowning in pre-existing findings.

```bash
auditor scan . --write-baseline .auditor/baseline.json   # snapshot current findings, then exit
auditor scan . --baseline .auditor/baseline.json         # report only NEW findings
auditor scan . --baseline .auditor/baseline.json --fail-on high   # CI gate fires only on new high+
```

Each finding is fingerprinted by `(file, rule, hash(offending text))` — **line-independent**, so a
finding survives edits elsewhere in the file, but genuinely new code is still reported. Filtering
runs before `--fail-on`, so the gate trips only on findings absent from the baseline.

### noqa suppression

Flake8-compatible, honored only in real comments (string/docstring text is ignored):

```python
risky()            # noqa                       — suppress every finding on this line
risky()            # noqa: PY-SEC-DANGEROUS-EVAL — suppress just that rule
# auditor: noqa                                  — suppress the whole file
# auditor: noqa: PY-SEC-HARDCODED-SECRET         — suppress one rule file-wide
```

`scan --no-noqa` ignores all directives (an un-silenceable sweep). Suppressed counts are
surfaced, never silent.

## Standards & configuration

Ships recognized **industry-standard rulesets** as the baseline and lets each repo tailor
them — the ruff/eslint model. Config lives in `[tool.auditor]` in `pyproject.toml` **or** a
standalone `.auditor/config.toml` (standalone wins on conflict).

```toml
[tool.auditor]
extends = "strict"                 # base | strict | pydantic | all-strict | a path
exclude = ["migrations/**"]
diff_base = "origin/main"          # what `scan --vs-base` diffs against

[tool.auditor.rules]
PY-TYPING-MISSING-HINTS = { severity = "high" }
PY-OOP-CONSTRUCTOR-WALL = { enabled = true, threshold = { oop = { wall_kwarg_min = 10 } } }
PY-OOP-DUPLICATE-BLOCK  = { threshold = { dry = { dup_block_min_statements = 2 } } }

[tool.auditor.categories]
security = { min_severity = "high" }
```

Every threshold-driven rule's floor is config-tunable, grouped by concern (each knob is a
self-documenting `Field` with a `ge=1` validation): `threshold.oop.wall_kwarg_min`,
`threshold.size.max_complexity`, `threshold.dry.dup_block_min_statements`,
`threshold.jsx.repeated_jsx_min`, … Because the cache keys each rule by `(content + that rule's
resolved config)`, changing one threshold re-runs only that rule on the next scan.

- **Profiles**: `base` (industry floor: security/**malware**/secrets/supply-chain/correctness/
  async/typing/config + cross-file dedup on; opinionated OOP/composition off), `strict` (adds
  OOP/composition + complexity),
  `pydantic`, `all-strict` (audits every role — tests included — at production strength).
- **Roles**: every file is classified `production | test | test_support | script | generated`
  from path + content. Test code is audited under a **relaxed** policy (assert-for-auth,
  hardcoded-secret, etc. are noise-by-design in tests) — flip it to full strength with
  `--strict-tests` or `test_mode = "strict"`.
- **Per-rule cache**: each rule has a fingerprint = `hash(detector version + its resolved
  config)`; cached findings are reused only when the file content hash **and** that rule's
  fingerprint match.

## Detectors

**70 Python rules** across `security` (Bandit/OWASP-mapped), `malware`, `secrets`,
`supply-chain`, `correctness`, `typing`, `async`, `config`, `oop-composition`, and `style` —
including DRY/composition rules (cross-file duplicate model/function, within-file duplicate
blocks, parallel siblings, field-by-field copying) and a `suggestion` tier of low-stakes nudges
below the severity ladder. Each carries a stable `rule_id`, a category, a default severity, and
(for security) `standard_refs` like `bandit:B602` / `owasp:A03`. `auditor rules list` enumerates
them. The `correctness`, `async`, `config`, and `typing` categories are **Python-only** — they
encode Python-specific semantics (event-loop blocking, `BaseSettings`, etc.); TypeScript and shell
carry their own categories (below). `security`, `malware`, `secrets`, and `supply-chain` span
languages where applicable.

**Malware** (`malware`, 30 rules across Python, TypeScript, and Bash — on by default in `base`,
for vetting dependencies, PR diffs, and untrusted repos): the patterns that turn a benign
primitive into an attack, keyed on the *combination* so real decode/fetch/path use stays quiet —
obfuscated exec (`eval`/`exec` of a base64/hex/zlib-decoded blob), remote exec (running a fetched
response body), reverse shells (socket→`dup2`, `/dev/tcp`, `nc -e`, `socat exec:`), download-and-run
(`curl … | sh`), in-memory shellcode loaders (executable-memory alloc **and** cast-to-function),
`pickle`/`__reduce__` RCE gadgets, dynamic imports/`require` of a *decoded* name, computed
`child_process` commands, crypto-miners (stratum / known miners), credential-path access, and
exfil to anonymous webhook/paste/tunnel endpoints (common C2 sinks). AST/tree-sitter based for
Python and TS, so a minified one-liner payload is caught the same as formatted code. Mostly
`blocking`; the path/blob/destructive heuristics are `candidate`s you judge.

**Secrets** (`secrets`, on by default): a committed-credential sweep for Python, TS, and shell
(`PY-`/`TS-`/`SH-SECRET-DETECTED`) — high-confidence, format-validated provider patterns (AWS,
GitHub, Stripe, Slack, OpenAI, Google, JWTs, database URIs, PEM private keys, and many
newer-wave providers). Tuned against a 700+-file real-repo corpus for a near-zero false-positive
rate; benign lookalikes (UUIDs, hashes, example URLs) are excluded.

**Supply-chain** (`supply-chain`, on by default): the install-time *code-execution* vectors —
npm lifecycle hooks (`preinstall`/`install`/`postinstall` in `package.json`, which auto-run on
`npm install`) via `MF-SUPPLY-INSTALL-HOOK`, and `setup.py` running process/network/eval at
module scope (executes on every `pip install`) via `PY-SUPPLY-SETUP-EXEC`. Manifests are
dispatched by filename, not suffix. Dependency-graph scanning (typosquat, version pinning,
transitive CVEs) is deliberately left to dedicated tools with live databases (Dependabot,
OSV-Scanner) — the auditor stays offline and deterministic.

**TypeScript / React** (`.ts/.tsx/.js/.jsx`, via the `ts` extra — tree-sitter): objective,
**framework-agnostic** rules only —

- **security** (`security`, OWASP-mapped): `dangerouslySetInnerHTML` with dynamic content,
  `target="_blank"` without `rel="noopener"`, `javascript:` URLs, `eval`/`new Function`.
- **accessibility** (`a11y`): non-interactive `onClick`, icon-only button / form control /
  `<iframe>` without a label, `<img>` without alt, `<a>` without href, positive `tabIndex`,
  `autoFocus`, redundant role, mouse handler without a keyboard equivalent.
- **size & complexity**: large file, too many props, JSX nested too deep (config-tunable).
- **structure** (`react`): multiple components per file, repeated sibling JSX → `.map()`,
  duplicate imports, array index used as a React `key` (reorder/insert reconciliation bug).
- **DRY / extraction** (`react`): a component with a large hook cluster → custom `use*` hook
  (`EXTRACTABLE-HOOK`); a pure helper nested in a component → module-level util
  (`EXTRACTABLE-HELPER`); near-twin functions/components differing only in constants →
  parameterize into one (`PARALLEL-SIBLING`).
- **cross-file dedup**: same normalized component/function shape across files → extract a
  shared one (`XFILE-DUP-COMPONENT`/`DUP-FUNCTION`); the same substantial hand-rolled JSX
  sub-tree inline in different components → extract a shared component (`XFILE-DUP-JSX-BLOCK`).

The auditor deliberately does **not** encode a design system: it never says "this should be
`<Badge>`" or "use the size prop" — that needs the project's primitive vocabulary, which is
the agent + design-system skill's judgment layer. The auditor surfaces the structural fact
(duplication, extractable unit, accessibility violation); you map it to your code.

**Bash / shell** (`.sh/.bash`, no extra needed — line/regex based): the `malware` + `secrets`
categories for install scripts and backdoors — `curl … | sh`, reverse shells (`/dev/tcp`,
`nc -e`, `mkfifo|nc`, `socat exec:`), fork bombs, decode-and-run (`base64 -d | sh`),
disk-destroyers (`rm -rf /`, `mkfs`, `dd of=/dev/…`), persistence implants (`authorized_keys`,
cron, shell-rc files), anti-forensics (history wipe, `setenforce 0`, `iptables -F`, log
truncation), credential exfil (a secret path piped to an outbound command), and exfil to
anonymous webhook/paste/tunnel sinks. `search`-based, so an embedded pattern in a packed
one-liner is still caught; full-line `#` comments are skipped so documentation describing an
attack doesn't self-flag.

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

Tools: `scan`, `report`, `manifest`, `discover`, `aggregate`, `rules_list`. The MCP `scan`
takes `severity` and `since` (audit only a branch's changes), so an agent reviewing a PR pulls
back just the changed files' findings — fewer tokens, same cross-file correctness.

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
uv run pytest            # 560 tests
uv run pytest --cov=auditor
uv run ruff check auditor tests && uv run ruff format --check auditor tests
```

The package is held to its own standard: registries and analyzers are classes, config is
typed `pydantic-settings`, the index is async (in-house worker thread, no third-party
driver), and `auditor scan auditor/` on its own source is clean apart from a few
intentional, explainable findings (worker-thread `BaseException` propagation, plugin-load
isolation).
