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
  re-parses only those 3. Editing one rule's threshold re-runs only that rule. The index is
  one shared db at `~/.auditor/index.db` (override with `$AUDITOR_HOME`), partitioned by repo —
  not a file per repo. Repo-authored input (`.auditor/config.toml`, `.auditor/plugins/`,
  `.auditor/baseline.json`) stays in the repo.
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
auditor scan . -i                    # --incremental: use/update the shared cache (~/.auditor/index.db)
auditor scan . -p strict             # --profile: run any repo at strict strength (no config edits)
auditor scan . -x '**/migrations/**' # --exclude: ad-hoc ignore glob (repeatable), on top of config
auditor scan tests/ -t               # --strict-tests: audit test code at full production strength
auditor scan . -vvv                  # -v/-vv/-vvv: log progress to stderr (files / detail / per-finding)
auditor report path/to/file.py       # single file, stateless (manifest + findings)
auditor manifest path/to/file.py     # AST manifest only (no detectors)
auditor discover .                   # list auditable files with their classified role
auditor aggregate . -o AUDIT.md      # roll the index up into AUDIT.md
auditor index repos                  # list every repo in the shared index (~/.auditor)
auditor index forget .               # drop this repo's cached index data (registry row + cascade)
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

### Persistent ignores

Mute findings without touching the source — stored in the shared index (`~/.auditor`), applied
automatically on every rescan (CLI **and** MCP), keyed by `rule_id` at three scopes:

```bash
auditor ignore add PY-SEC-WEAK-HASH                              # repo-wide
auditor ignore add PY-SEC-WEAK-HASH --file src/legacy.py        # one file
auditor ignore add PY-SEC-WEAK-HASH --file src/legacy.py --line 42 --reason "vetted"
auditor ignore list                                            # show entries + ids
auditor ignore rm 3                                            # unignore by id …
auditor ignore rm PY-SEC-WEAK-HASH --file src/legacy.py        # … or by selector
auditor ignore clear                                          # drop all for this repo
```

A line-level add snapshots the offending text, so the ignore follows the code when lines shift
and re-surfaces only if that code changes. Ignored findings are hidden from `scan`/`report`/
`aggregate` (with an `(N ignored)` count) and don't trip `--fail-on`; `scan --show-ignored`
reveals them. Same surface over MCP: `ignore_add` / `ignore_list` / `ignore_remove`, and
`scan(show_ignored=…)`. Unlike `noqa` (in-source, shared via git) and `--baseline` (a committed
snapshot), ignores are local to your machine's index.

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

### Rule reference

The full registry (`auditor rules list` for JSON, `--category`/`--standard` to filter). Verdict
`auto` = the tool decided (gates CI); `candidate` = evidence for the agent to judge.

<details>
<summary><b>All 123 rules</b> (generated from <code>auditor rules list</code>)</summary>

#### security (23)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `PY-SEC-ASSERT-FOR-SECURITY` | medium | candidate | bandit:B101, owasp:A04 |
| `PY-SEC-BIND-ALL-INTERFACES` | low | auto | bandit:B104, owasp:A05 |
| `PY-SEC-DANGEROUS-EVAL` | blocking | auto | bandit:B307, owasp:A03 |
| `PY-SEC-DJANGO-RAW-SQL` | high | candidate | owasp:A03 |
| `PY-SEC-FLASK-DEBUG` | medium | auto | bandit:B201, owasp:A05 |
| `PY-SEC-HARDCODED-SECRET` | high | auto | bandit:B105, owasp:A07 |
| `PY-SEC-INSECURE-RANDOM` | medium | candidate | bandit:B311, owasp:A02 |
| `PY-SEC-INSECURE-TEMPFILE` | medium | auto | bandit:B306, bandit:B108, owasp:A05 |
| `PY-SEC-INSECURE-TLS` | high | auto | bandit:B501, owasp:A02 |
| `PY-SEC-JINJA-AUTOESCAPE-OFF` | medium | auto | bandit:B701, owasp:A03 |
| `PY-SEC-PARAMIKO-AUTOADD` | medium | auto | bandit:B507, owasp:A07 |
| `PY-SEC-PATH-TRAVERSAL` | medium | candidate | owasp:A01 |
| `PY-SEC-REQUEST-NO-TIMEOUT` | medium | auto | bandit:B113, owasp:A06 |
| `PY-SEC-SHELL-INJECTION` | high | auto | bandit:B602, bandit:B605, owasp:A03 |
| `PY-SEC-SQL-STRING-BUILD` | high | candidate | bandit:B608, owasp:A03 |
| `PY-SEC-SSRF` | medium | candidate | owasp:A10 |
| `PY-SEC-UNSAFE-DESERIALIZE` | high | auto | bandit:B301, bandit:B506, owasp:A08 |
| `PY-SEC-WEAK-HASH` | medium | auto | bandit:B303, bandit:B324, owasp:A02 |
| `PY-SEC-XXE-UNSAFE-XML` | medium | auto | bandit:B313, owasp:A05 |
| `TS-SEC-DANGEROUS-EVAL` | high | auto | owasp:A03 |
| `TS-SEC-DANGEROUS-HTML` | high | candidate | owasp:A03 |
| `TS-SEC-JAVASCRIPT-URL` | high | auto | owasp:A03 |
| `TS-SEC-TARGET-BLANK-NOOPENER` | medium | auto | owasp:A05 |

#### malware (30)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `PY-MAL-CREDENTIAL-ACCESS` | high | candidate | — |
| `PY-MAL-CRYPTO-MINER` | high | auto | — |
| `PY-MAL-DOWNLOAD-EXEC` | high | auto | — |
| `PY-MAL-DYNAMIC-IMPORT` | medium | candidate | — |
| `PY-MAL-ENCODED-BLOB` | medium | candidate | — |
| `PY-MAL-EXFIL-URL` | medium | candidate | — |
| `PY-MAL-OBFUSCATED-EXEC` | blocking | auto | — |
| `PY-MAL-PICKLE-REDUCE` | high | candidate | — |
| `PY-MAL-REMOTE-EXEC` | blocking | auto | — |
| `PY-MAL-REVERSE-SHELL` | blocking | auto | — |
| `PY-MAL-SHELLCODE` | blocking | auto | — |
| `SH-MAL-ANTIFORENSICS` | high | candidate | — |
| `SH-MAL-CREDENTIAL-EXFIL` | high | candidate | — |
| `SH-MAL-CRYPTO-MINER` | high | auto | — |
| `SH-MAL-CURL-BASH` | high | auto | — |
| `SH-MAL-DESTRUCTIVE` | high | candidate | — |
| `SH-MAL-ENCODED-EXEC` | blocking | auto | — |
| `SH-MAL-EXFIL-URL` | medium | candidate | — |
| `SH-MAL-FORK-BOMB` | blocking | auto | — |
| `SH-MAL-PERSISTENCE` | high | candidate | — |
| `SH-MAL-REVERSE-SHELL` | blocking | auto | — |
| `TS-MAL-CREDENTIAL-ACCESS` | high | candidate | — |
| `TS-MAL-CRYPTO-MINER` | high | auto | — |
| `TS-MAL-DOWNLOAD-EXEC` | high | auto | — |
| `TS-MAL-DYNAMIC-REQUIRE` | medium | candidate | — |
| `TS-MAL-ENCODED-BLOB` | medium | candidate | — |
| `TS-MAL-EXEC-INJECTION` | high | candidate | — |
| `TS-MAL-EXFIL-URL` | medium | candidate | — |
| `TS-MAL-OBFUSCATED-EXEC` | blocking | auto | — |
| `TS-MAL-REMOTE-EXEC` | blocking | auto | — |

#### secrets (3)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `PY-SECRET-DETECTED` | high | auto | — |
| `SH-SECRET-DETECTED` | high | auto | — |
| `TS-SECRET-DETECTED` | high | auto | — |

#### supply-chain (2)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `MF-SUPPLY-INSTALL-HOOK` | medium | candidate | — |
| `PY-SUPPLY-SETUP-EXEC` | medium | candidate | — |

#### correctness (4)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `PY-CORRECT-BROAD-EXCEPT` | medium | auto | — |
| `PY-CORRECT-NAIVE-DATETIME` | suggestion | candidate | — |
| `PY-CORRECT-RAISE-WITHOUT-FROM` | low | candidate | — |
| `PY-CORRECT-SWALLOWED-EXCEPTION` | medium | candidate | — |

#### async (6)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `PY-ASYNC-DANGLING-TASK` | high | auto | — |
| `PY-ASYNC-NO-AWAIT-BODY` | low | candidate | — |
| `PY-ASYNC-SEQUENTIAL-AWAITS` | low | candidate | — |
| `PY-ASYNC-SYNC-IO` | high | candidate | — |
| `PY-ASYNC-UNAWAITED-COROUTINE` | high | auto | — |
| `PY-ASYNC-UNLOCKED-LAZY-INIT` | high | candidate | — |

#### config (2)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `PY-CONFIG-ADHOC-ENV` | low | auto | — |
| `PY-CONFIG-IMPORT-TIME-IO` | medium | candidate | — |

#### typing (2)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `PY-TYPING-MISSING-HINTS` | low | auto | — |
| `PY-TYPING-UNTYPED-DICT` | medium | auto | — |

#### oop-composition (20)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `PY-OOP-BUILDER-CLASS` | low | candidate | — |
| `PY-OOP-CLOSURE-CAPTURE` | suggestion | candidate | — |
| `PY-OOP-CONSTRUCTOR-WALL` | low | candidate | — |
| `PY-OOP-DATACLASS-IN-PYDANTIC` | medium | auto | — |
| `PY-OOP-DICT-MUTATION-BUILDER` | suggestion | candidate | — |
| `PY-OOP-DISPATCH-LADDER` | low | candidate | — |
| `PY-OOP-DUPLICATE-BLOCK` | low | candidate | — |
| `PY-OOP-FIELD-COPY` | low | candidate | — |
| `PY-OOP-FLAT-FIELD-MODEL` | low | candidate | — |
| `PY-OOP-FREE-FN-ORCHESTRATOR` | low | candidate | — |
| `PY-OOP-GOD-CLASS` | low | candidate | — |
| `PY-OOP-HIGH-COMPLEXITY` | low | candidate | — |
| `PY-OOP-LONG-PARAM-LIST` | low | candidate | — |
| `PY-OOP-MODEL-REBUILD` | suggestion | candidate | — |
| `PY-OOP-MODULE-CONST-FOR-SUBCLASS` | suggestion | candidate | — |
| `PY-OOP-PARALLEL-SIBLING` | low | candidate | — |
| `PY-OOP-STATIC-METHOD-CLASS` | low | candidate | — |
| `PY-OOP-THIN-WRAPPER` | low | candidate | — |
| `PY-XFILE-DUP-FUNCTION` | low | candidate | — |
| `PY-XFILE-DUP-MODEL` | low | candidate | — |

#### style (6)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `PY-STYLE-FILE-SIZE` | low | auto | — |
| `PY-STYLE-IF-FALSE-IMPORT` | low | auto | — |
| `PY-STYLE-INLINE-IMPORT` | medium | auto | — |
| `PY-STYLE-STALE-COMMENT` | low | candidate | — |
| `TS-STYLE-DUPLICATE-IMPORT` | low | auto | — |
| `TS-STYLE-FILE-SIZE` | low | auto | — |

#### react (11)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `TS-REACT-ARRAY-INDEX-KEY` | medium | candidate | — |
| `TS-REACT-DEEP-JSX-NESTING` | low | candidate | — |
| `TS-REACT-EXTRACTABLE-HELPER` | low | candidate | — |
| `TS-REACT-EXTRACTABLE-HOOK` | low | candidate | — |
| `TS-REACT-MULTI-COMPONENT-FILE` | low | candidate | — |
| `TS-REACT-PARALLEL-SIBLING` | low | candidate | — |
| `TS-REACT-REPEATED-JSX` | low | candidate | — |
| `TS-REACT-TOO-MANY-PROPS` | low | candidate | — |
| `TS-XFILE-DUP-COMPONENT` | low | candidate | — |
| `TS-XFILE-DUP-FUNCTION` | low | candidate | — |
| `TS-XFILE-DUP-JSX-BLOCK` | low | candidate | — |

#### a11y (11)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `TS-A11Y-ANCHOR-NO-HREF` | medium | candidate | — |
| `TS-A11Y-AUTOFOCUS` | low | candidate | — |
| `TS-A11Y-DECORATIVE-ICON` | low | candidate | — |
| `TS-A11Y-FORM-LABEL` | medium | candidate | — |
| `TS-A11Y-ICON-BUTTON-NO-LABEL` | medium | candidate | — |
| `TS-A11Y-IFRAME-TITLE` | medium | candidate | — |
| `TS-A11Y-IMG-NO-ALT` | medium | candidate | — |
| `TS-A11Y-MOUSE-NO-KEY` | medium | candidate | — |
| `TS-A11Y-NONINTERACTIVE-ONCLICK` | medium | candidate | — |
| `TS-A11Y-POSITIVE-TABINDEX` | medium | candidate | — |
| `TS-A11Y-REDUNDANT-ROLE` | low | candidate | — |

#### design-system (3)

| rule_id | severity | verdict | standards |
|---|---|---|---|
| `TS-DS-DIRECT-UI-IMPORT` | medium | candidate | — |
| `TS-DS-INLINE-PRIMITIVE` | low | candidate | — |
| `TS-DS-SIZE-OVERRIDE` | low | candidate | — |

</details>

## Plugins

Extend by subclassing — the ABCs (`Detector`, `LanguageAuditor`, `Reporter`) auto-register.
Three discovery mechanisms: **entry points** (`auditor.detectors`, …), **config-named
modules** (`[tool.auditor] plugins = ["acme.rules"]`), and local **`.auditor/plugins/*.py`**
(gated behind `trust_local_plugins`/`--allow-local-plugins` — importing them runs code).

## MCP server

The auditor ships a stdio [MCP](https://modelcontextprotocol.io) server so agents can call it
directly. Install the `mcp` extra (`uv tool install ".[mcp]"`) — it puts `auditor-mcp` on your
PATH:

```bash
auditor-mcp                       # stdio MCP server (or: python -m auditor.mcp_server)
```

Tools: `scan`, `report`, `manifest`, `discover`, `aggregate`, `rules_list`, `ignore_add`,
`ignore_list`, `ignore_remove`. The MCP `scan`
takes `severity` and `since` (audit only a branch's changes), so an agent reviewing a PR pulls
back just the changed files' findings — fewer tokens, same cross-file correctness.

### Claude Code

`claude mcp add` registers the stdio server; everything after `--` is the launch command:

```bash
claude mcp add auditor -- auditor-mcp                      # local scope (this project, private)
claude mcp add --scope user auditor -- auditor-mcp         # all your projects
claude mcp add --scope project auditor -- auditor-mcp      # shared via .mcp.json (committed)
```

Project scope writes a `.mcp.json` you can commit so teammates get it automatically:

```json
{
  "mcpServers": {
    "auditor": { "command": "auditor-mcp", "args": [] }
  }
}
```

If `auditor-mcp` isn't on PATH (not installed as a tool), run it through uv from the checkout:

```bash
claude mcp add auditor -- uv run --directory /path/to/auditor auditor-mcp
```

Verify with `claude mcp list`, then ask Claude to "scan this repo with the auditor MCP".

### Codex CLI

`codex mcp add` mirrors the same `-- <command>` syntax:

```bash
codex mcp add auditor -- auditor-mcp
```

Or add it to `~/.codex/config.toml` (a project-scoped `.codex/config.toml` works in trusted
projects too):

```toml
[mcp_servers.auditor]
command = "auditor-mcp"
args = []
# env = { AUDITOR_HOME = "/home/you/.auditor" }   # optional: pin the shared index location
```

### Docker (no local Python/uv needed)

Build once, then point either client at the container — the repo is mounted at `/auditor` and the
index persists in a named volume:

```bash
docker build -t auditor:latest .                  # or: docker compose build
```

```bash
# Claude Code
claude mcp add auditor -- docker run -i --rm \
  -v "$PWD:/auditor" -v auditor-index:/root/.auditor \
  --entrypoint auditor-mcp auditor:latest
```

```toml
# Codex ~/.codex/config.toml
[mcp_servers.auditor]
command = "docker"
args = ["run", "-i", "--rm",
        "-v", "${PWD}:/auditor", "-v", "auditor-index:/root/.auditor",
        "--entrypoint", "auditor-mcp", "auditor:latest"]
```

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
docker compose run --rm -T auditor-mcp                 # stdio MCP server (see MCP server § above)
```

The image bundles the `mcp` + `ts` extras, and the incremental index persists in the
`auditor-index` named volume so repeat scans stay fast.

## Development

```bash
uv run pytest            # 931 tests
uv run pytest --cov=auditor
uv run ruff check auditor tests && uv run ruff format --check auditor tests
```

The package is held to its own standard: registries and analyzers are classes, config is
typed `pydantic-settings`, the index is async (in-house worker thread, no third-party
driver), and `auditor scan auditor/` on its own source is clean apart from a few
intentional, explainable findings (worker-thread `BaseException` propagation, plugin-load
isolation).
