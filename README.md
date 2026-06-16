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

**Recommended — install the CLI globally as a tool** (so `auditr` is on your PATH in any repo):

```bash
uv tool install auditr             # from PyPI (distribution name is `auditr`)
uv tool install .                  # from a checkout
uv tool install git+https://github.com/Sung96kim/auditor   # from GitHub
uv tool install "auditr[mcp]"      # include the FastMCP server (auditr-mcp)
uv tool install "auditr[ts]"       # include TypeScript/React support (tree-sitter)
```

**With pip / pipx:**

```bash
pip install auditr                 # into the active environment
pip install "auditr[mcp,ts]"       # with the MCP server + TypeScript support
pipx install auditr                # isolated global install (like uv tool)
```

The command is `auditr` (with `auditr-mcp` for the MCP server); `auditor`/`auditor-mcp`
are kept as aliases. The PyPI distribution is named `auditr` because `auditor` was taken,
so you `pip install auditr` but run `auditr`.

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
auditor scan . -x '**/vendor/**'     # --exclude: ad-hoc ignore glob (repeatable), on top of config
auditor scan . --include-gitignored  # also audit git-ignored files (skipped by default)
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
auditor scan . --rule SA-RAW-SQL     # --rule: only these rule ids (repeatable); typos get a "did you mean?"
auditor scan . --config-json '{"sqlalchemy":{"expire_on_commit":true}}'  # inject config overrides (highest layer)
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
finding survives edits elsewhere in the file, but genuinely new code is still reported. Fingerprints
are counted, not just set-membership: if a file legitimately has three untyped `def __init__(`, all
three are recorded and a **fourth** one you add later still surfaces. Filtering runs before
`--fail-on`, so the gate trips only on findings absent from the baseline. (Baselines written before
this counting change under-recorded shared snippets — regenerate with `--write-baseline`.)

### skip suppression

An auditor-native directive (its own namespace, so rule codes never collide with ruff/flake8's
`# noqa`), honored only in real comments — string/docstring text is ignored, and `#`/`//` both work:

```python
risky()  # auditor: skip                          — suppress every finding on this line
risky()  # auditor: skip: PY-SEC-DANGEROUS-EVAL    — suppress just that rule (comma-separate more)
# auditor: skip-file                              — suppress the whole file
# auditor: skip-file: PY-SEC-HARDCODED-SECRET      — suppress one rule file-wide
```

`scan --no-skips` ignores all directives (an un-silenceable sweep). Suppressed counts are surfaced,
never silent. (Plain `# noqa` is **not** honored by the auditor — it stays yours and ruff/flake8's.)

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

`ignore add` validates the `rule_id` against the registry — it loads the repo's config first, so
plugin-contributed rules (entry-point/config, and trusted or `--allow-local-plugins` local plugins)
are recognized like built-ins; `--force` skips the check entirely. A line-level add snapshots the
offending text, so the ignore follows the code when
lines shift and re-surfaces only if that code changes. Ignored findings are hidden from `scan`/`report`/
`aggregate` (with an `(N ignored)` count) and don't trip `--fail-on`; `scan --show-ignored`
reveals them. Same surface over MCP: `ignore_add` / `ignore_list` / `ignore_remove`, and
`scan(show_ignored=…)`. Unlike `auditor: skip` (in-source, shared via git) and `--baseline` (a
committed snapshot), ignores are local to your machine's index.

## Standards & configuration

Ships recognized **industry-standard rulesets** as the baseline and lets each repo tailor
them — the ruff/eslint model. Config lives in `[tool.auditor]` in `pyproject.toml` **or** a
standalone `.auditor/config.toml` (standalone wins on conflict). Any setting can also be overridden
ad-hoc with `--config-json '<json>'` (deep-merged as the highest layer, validated) — no file edits,
handy for CI and experiments; the MCP `scan` tool takes the same as a `config` dict.

```toml
[tool.auditor]
extends = "strict"                 # base | strict | pydantic | all-strict | a path
exclude = ["vendor/**", "legacy/**"]  # extra globs to skip, on top of the defaults below
respect_gitignore = true           # skip git-ignored files (CLI: --include-gitignored overrides)
diff_base = "origin/main"          # what `scan --vs-base` diffs against

[tool.auditor.rules]
PY-TYPING-MISSING-HINTS = { severity = "high" }
PY-OOP-CONSTRUCTOR-WALL = { enabled = true, threshold = { oop = { wall_kwarg_min = 10 } } }
PY-OOP-DUPLICATE-BLOCK  = { threshold = { dry = { dup_block_min_statements = 2 } } }

[tool.auditor.categories]
security = { min_severity = "high" }
```

**What a scan skips by default.** Generated/vendored files (`*_pb2.py`, `*.gen.ts`, `*.d.ts`, …),
cache/build dirs (`node_modules`, `.venv`, `__pycache__`, `dist`, …), and **git-ignored files** are
dropped. Migration directories (`**/migrations/**`, `**/alembic/versions/**`) are *soft*-skipped:
left out of a whole-repo scan, but audited when you point at them directly (`auditor scan
app/migrations`). To include git-ignored files, set `respect_gitignore = false` or pass
`--include-gitignored`.

Every threshold-driven rule's floor is config-tunable, grouped by concern (each knob is a
self-documenting `Field` with a `ge=1` validation): `threshold.oop.wall_kwarg_min`,
`threshold.size.max_complexity`, `threshold.dry.dup_block_min_statements`,
`threshold.jsx.repeated_jsx_min`, … Because the cache keys each rule by `(content + that rule's
resolved config)`, changing one threshold re-runs only that rule on the next scan.

### Framework-aware test rules (pytest)

Structural test-quality checks that complement (never duplicate) ruff and pytest. They fire only
on test-role Python files and are all `candidate` (advisory — they never gate CI):

| rule | catches |
|---|---|
| `PY-TEST-PARAMETRIZE-CANDIDATE` | N near-identical tests differing only in literals → `@pytest.mark.parametrize` |
| `PY-TEST-NO-ASSERTION` | a test that asserts nothing |
| `PY-TEST-LOGIC-IN-TEST` | `if`/`for`/`while`/`try` in a test body |
| `PY-TEST-OVER-MOCKING` | too many mocks in one test (`threshold.test.max_mocks_per_test`) |
| `PY-TEST-DUPLICATE-SETUP` | a repeated arrange block across tests → extract a fixture |
| `PY-TEST-UNUSED-FIXTURE` | a fixture defined but never requested (repo-level) |
| `PY-TEST-SKIP-NO-REASON` | `@pytest.mark.skip/skipif/xfail` without `reason=` |
| `PY-TEST-SLEEP` | `time.sleep()` in a test |
| `PY-TEST-FIXTURE-MUTABLE-WIDE-SCOPE` | a `session`/`module`/`package`-scoped fixture returning a mutable literal (`[]`/`{}`) — shared state leaks across tests |

List them with `auditor rules list --framework pytest`. Tune floors under `[tool.auditor.threshold.test]`.

### Dead code (`PY-DEAD-SYMBOL`)

Repo-level (category `dead-code`, `candidate` — advisory, never gates CI): a module-level
**private function/class** (`_name`) or **constant** defined but never referenced anywhere in the
repo. Complements ruff, which only flags unused *imports*/*locals* — not a cross-file dead symbol.
FP-safe (name-based): a name used anywhere — incl. in a string literal, `__all__`, or a pyproject
entry point — counts as used; `__init__.py` defs and framework-magic globals (`down_revision`,
`pytestmark`, …) are exempt. Findings are emitted for production/script code; references are pooled
repo-wide. The cross-file pass is language-agnostic, so a `TS-DEAD-SYMBOL` sibling can drop in later.

### SQLAlchemy (`framework="sqlalchemy"`)

Per-file ORM rules (fire only in files that import `sqlalchemy`; all `candidate`):
`SA-MUTABLE-DEFAULT` (shared mutable column default — use a callable, not `default=[]`),
`SA-LAZY-DYNAMIC` (`relationship(lazy="dynamic")` — async-incompatible), `SA-NAIVE-DATETIME-DEFAULT`,
`SA-RAW-SQL` (interpolated `text()`/`execute()` — injection), `SA-ASYNC-EXPIRE-ON-COMMIT`
(async session factory missing `expire_on_commit=False` → `MissingGreenlet`), and
`SA-JOINED-COLLECTION` (`relationship(lazy="joined")` on a `Mapped[list[...]]` collection →
cartesian-product JOIN; use `selectin`).

Two more are **off by default** — the auditor can't see your session factory (often in a shared
lib), so declare facts about it to activate them:

```toml
[tool.auditor.sqlalchemy]
expire_on_commit = true   # activates SA-GREENLET-ATTR-AFTER-COMMIT (attr access after commit())
async_session = true      # activates SA-IMPLICIT-LAZY-ASYNC (relationship() with no explicit lazy=)
```

`SA-IMPLICIT-LAZY-ASYNC` flags `relationship()` calls that don't set `lazy=` explicitly: the
default `"select"` emits a synchronous SELECT on attribute access, which raises `MissingGreenlet`
under `AsyncSession`. List them all with `auditor rules list --framework sqlalchemy`.

`SA-GREENLET-ATTR-AFTER-COMMIT` won't fire if the object is refreshed before the access — including
through a helper. By default the auditor resolves helpers defined **in the repo**; to also follow
helpers from a first-party dependency (e.g. a shared `refresh_orms(session, objs)`), list its
package prefix so the resolver may read its installed source from the project's `.venv`:

```toml
[tool.auditor]
resolve_packages = ["atmosphere"]   # follow callees into these installed packages (default: none)
```

Resolution is repo-local by default; `resolve_packages` is opt-in and read from the *scanned
project's* environment. If it's set but no env is found, the scan warns (dependency resolution is
then off — `commit(); refresh_orms(...); use obj` may surface as a false positive).

### Pydantic (`framework="pydantic"`)

Per-file rules gated to files that import `pydantic`: `PY-PYDANTIC-V1-CONFIG-CLASS` (`candidate`) —
a `BaseModel` configured via an inner `class Config:` instead of `model_config = ConfigDict(...)`;
v2 keeps the inner class as a deprecated shim but silently ignores misspelled keys (`orm_mode` vs
`from_attributes`). (`PY-OOP-DATACLASS-IN-PYDANTIC` is also pydantic-aware.)

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

**90 Python rules** across `security` (Bandit/OWASP-mapped), `malware`, `secrets`,
`supply-chain`, `correctness`, `typing`, `async`, `config`, `dead-code`, `testing`,
`oop-composition`, and `style` (plus the `sqlalchemy`/`pydantic` framework rules) —
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
<summary><b>All 146 rules</b> (generated from <code>auditor rules list</code>)</summary>

#### security (24)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-SEC-ASSERT-FOR-SECURITY` | medium | candidate | `assert` used for a security check (stripped under `python -O`) |
| `PY-SEC-BIND-ALL-INTERFACES` | low | auto | a socket bound to `0.0.0.0` — exposes the service on every interface |
| `PY-SEC-DANGEROUS-EVAL` | blocking | auto | `eval`/`exec`/`compile` on non-constant input — arbitrary code execution |
| `PY-SEC-DJANGO-RAW-SQL` | high | candidate | Django `.raw()`/`.extra()` with caller-supplied SQL — injection |
| `PY-SEC-FLASK-DEBUG` | medium | auto | Flask `debug=True` — exposes the Werkzeug debugger (RCE) |
| `PY-SEC-HARDCODED-SECRET` | high | auto | a literal assigned to a password/token/api_key-named variable |
| `PY-SEC-INSECURE-RANDOM` | medium | candidate | `random` used for security-sensitive values (tokens/keys) |
| `PY-SEC-INSECURE-TEMPFILE` | medium | auto | `tempfile.mktemp()` or a hardcoded `/tmp` path — TOCTOU race |
| `PY-SEC-INSECURE-TLS` | high | auto | TLS verification disabled (`verify=False`) or hostname checks off |
| `PY-SEC-JINJA-AUTOESCAPE-OFF` | medium | auto | a Jinja `Environment` built without `autoescape=True` — XSS |
| `PY-SEC-PARAMIKO-AUTOADD` | medium | auto | Paramiko `AutoAddPolicy`/`WarningPolicy` — accepts unknown host keys |
| `PY-SEC-PATH-TRAVERSAL` | medium | candidate | a file path built from external input — possible traversal |
| `PY-SEC-REQUEST-NO-TIMEOUT` | medium | auto | an HTTP request with no `timeout` — can hang forever |
| `PY-SEC-SHELL-INJECTION` | high | auto | `os.system`/`os.popen` or `subprocess(..., shell=True)` |
| `PY-SEC-SQL-STRING-BUILD` | high | candidate | SQL built from caller values passed to `.execute()` — injection |
| `PY-SEC-SSRF` | medium | candidate | an outbound request to a caller-derived URL — possible SSRF |
| `PY-SEC-UNSAFE-DESERIALIZE` | high | auto | `pickle`/`yaml.unsafe_load` on untrusted data — code execution |
| `PY-SEC-WEAK-HASH` | medium | auto | md5/sha1 for integrity/passwords (honors `usedforsecurity=False`) |
| `PY-SEC-XXE-UNSAFE-XML` | medium | auto | XML parsed without `defusedxml` — XXE / entity expansion |
| `SA-RAW-SQL` | high | candidate | interpolated `text()`/`execute()` SQL — injection (numeric interpolation exempt) |
| `TS-SEC-DANGEROUS-EVAL` | high | auto | `eval`/`new Function`/`setTimeout(string)` — code injection |
| `TS-SEC-DANGEROUS-HTML` | high | candidate | `dangerouslySetInnerHTML` with non-constant HTML — XSS |
| `TS-SEC-JAVASCRIPT-URL` | high | auto | a `javascript:` URL in `href`/`src`/`to` — script injection |
| `TS-SEC-TARGET-BLANK-NOOPENER` | medium | auto | `target="_blank"` without `rel="noopener"` — reverse tabnabbing |

#### malware (30)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-MAL-CREDENTIAL-ACCESS` | high | candidate | a known credential path (`~/.ssh`, `.aws/credentials`, …) flowing into a read sink |
| `PY-MAL-CRYPTO-MINER` | high | auto | a known crypto-miner / stratum-pool signature in a string literal |
| `PY-MAL-DOWNLOAD-EXEC` | high | auto | a downloaded script piped straight to a shell (`curl … | sh`) |
| `PY-MAL-DYNAMIC-IMPORT` | medium | candidate | `__import__` of a base64/hex/char-decoded (hidden) module name |
| `PY-MAL-ENCODED-BLOB` | medium | candidate | a long base64/hex literal — a packed/encoded payload |
| `PY-MAL-EXFIL-URL` | medium | candidate | a URL to an anonymous paste/tunnel/webhook (common C2/exfil sink) |
| `PY-MAL-OBFUSCATED-EXEC` | blocking | auto | `eval`/`exec` of a base64/hex/zlib-decoded blob |
| `PY-MAL-PICKLE-REDUCE` | high | candidate | `__reduce__` returning a code-exec callable — pickle RCE gadget |
| `PY-MAL-REMOTE-EXEC` | blocking | auto | `eval`/`exec` of a fetched network response body |
| `PY-MAL-REVERSE-SHELL` | blocking | auto | a socket wired to a shell (`dup2`+`fileno`, `pty.spawn`) |
| `PY-MAL-SHELLCODE` | blocking | auto | a buffer cast to a function after executable-memory alloc — shellcode loader |
| `SH-MAL-ANTIFORENSICS` | high | candidate | history wipe, `setenforce 0`/`iptables -F`, log truncation — trace evasion |
| `SH-MAL-CREDENTIAL-EXFIL` | high | candidate | a secret path piped to an outbound command |
| `SH-MAL-CRYPTO-MINER` | high | auto | a crypto-miner / pool signature |
| `SH-MAL-CURL-BASH` | high | auto | a downloaded script piped to a shell (`curl … | sh`) |
| `SH-MAL-DESTRUCTIVE` | high | candidate | `rm -rf /`, `mkfs`, `dd of=/dev/…` — host wipe |
| `SH-MAL-ENCODED-EXEC` | blocking | auto | `base64 -d | sh` — obfuscated code execution |
| `SH-MAL-EXFIL-URL` | medium | candidate | a URL to an anonymous paste/tunnel/webhook (C2 sink) |
| `SH-MAL-FORK-BOMB` | blocking | auto | a fork bomb (`:(){ :|:& };:`) — process-table exhaustion |
| `SH-MAL-PERSISTENCE` | high | candidate | an autostart implant (`authorized_keys`, cron, rc file) |
| `SH-MAL-REVERSE-SHELL` | blocking | auto | reverse-shell wiring (`/dev/tcp`, `nc -e`, `mkfifo`, `socat`) |
| `TS-MAL-CREDENTIAL-ACCESS` | high | candidate | a known credential path read (potential harvesting) |
| `TS-MAL-CRYPTO-MINER` | high | auto | a crypto-miner / pool signature |
| `TS-MAL-DOWNLOAD-EXEC` | high | auto | a fetched script passed to `eval` — remote code execution |
| `TS-MAL-DYNAMIC-REQUIRE` | medium | candidate | `require()` of a computed/decoded value |
| `TS-MAL-ENCODED-BLOB` | medium | candidate | a base64/hex blob — possible packed payload |
| `TS-MAL-EXEC-INJECTION` | high | candidate | `child_process` exec/spawn of a computed command |
| `TS-MAL-EXFIL-URL` | medium | candidate | a URL to an anonymous paste/tunnel/webhook (C2/exfil sink) |
| `TS-MAL-OBFUSCATED-EXEC` | blocking | auto | `eval`/`Function` of an `atob`/base64-decoded payload |
| `TS-MAL-REMOTE-EXEC` | blocking | auto | `eval`/`Function` of a fetched response body |

#### secrets (3)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-SECRET-DETECTED` | high | auto | a committed, format-validated provider credential in Python source |
| `SH-SECRET-DETECTED` | high | auto | a committed, format-validated provider credential in a shell script |
| `TS-SECRET-DETECTED` | high | auto | a committed, format-validated provider credential in TS/JS source |

#### supply-chain (2)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `MF-SUPPLY-INSTALL-HOOK` | medium | candidate | an npm `preinstall`/`install`/`postinstall` script (runs on `npm install`) |
| `PY-SUPPLY-SETUP-EXEC` | medium | candidate | `setup.py` running process/network/eval at module scope (runs on `pip install`) |

#### correctness (9)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-CORRECT-BROAD-EXCEPT` | medium | auto | a bare/`Exception`/`BaseException` catch that doesn't re-raise |
| `PY-CORRECT-NAIVE-DATETIME` | suggestion | candidate | `datetime.now()`/`utcnow()` without tz — a naive timestamp |
| `PY-CORRECT-RAISE-WITHOUT-FROM` | low | candidate | raising inside `except` without `from` — loses the cause |
| `PY-CORRECT-SWALLOWED-EXCEPTION` | medium | candidate | an `except` that silently `pass`es — error swallowed |
| `PY-PYDANTIC-V1-CONFIG-CLASS` | medium | candidate | a `BaseModel` using inner `class Config` (v2 ignores misspelled keys) |
| `SA-JOINED-COLLECTION` | medium | auto | `lazy="joined"` on a `Mapped[list]` — cartesian-product JOIN |
| `SA-LAZY-DYNAMIC` | low | candidate | `relationship(lazy="dynamic"/"subquery")` — async-incompatible |
| `SA-MUTABLE-DEFAULT` | medium | candidate | a shared mutable column `default=[]/{}` — use a callable |
| `SA-NAIVE-DATETIME-DEFAULT` | low | candidate | a naive datetime column default with no `server_default` |

#### async (9)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-ASYNC-DANGLING-TASK` | high | auto | a `create_task`/`ensure_future` result discarded — task may be GC'd mid-flight |
| `PY-ASYNC-NO-AWAIT-BODY` | low | candidate | an `async def` with no await/async-with/async-for — make it sync |
| `PY-ASYNC-SEQUENTIAL-AWAITS` | low | candidate | awaits inside a loop that could be `gather`-ed concurrently |
| `PY-ASYNC-SYNC-IO` | high | candidate | synchronous/blocking I/O in an async function — blocks the event loop |
| `PY-ASYNC-UNAWAITED-COROUTINE` | high | auto | a coroutine call never awaited — silently does nothing |
| `PY-ASYNC-UNLOCKED-LAZY-INIT` | high | candidate | check-then-set lazy init with no lock — concurrent race |
| `SA-ASYNC-EXPIRE-ON-COMMIT` | medium | candidate | an async session factory missing `expire_on_commit=False` — MissingGreenlet |
| `SA-GREENLET-ATTR-AFTER-COMMIT` | medium | candidate | an ORM attribute accessed after `commit()` expired it (AsyncSession) |
| `SA-IMPLICIT-LAZY-ASYNC` | medium | candidate | `relationship()` with no explicit `lazy=` — sync lazy-load under AsyncSession |

#### config (3)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-CONFIG-ADHOC-ENV` | low | auto | an ad-hoc `os.environ`/`getenv` read (well-known OS vars exempt) — use BaseSettings |
| `PY-CONFIG-IMPORT-TIME-IO` | medium | candidate | network/file I/O at module import — side-effectful import |
| `PY-CONFIG-SCATTERED-SETTINGS` | low | candidate | a `BaseSettings` subclass defined outside the settings home module |

#### typing (2)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-TYPING-MISSING-HINTS` | low | auto | a function parameter or return without a type annotation |
| `PY-TYPING-UNTYPED-DICT` | medium | auto | a `dict[str, Any]` param/return instead of a typed model |

#### dead-code (1)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-DEAD-SYMBOL` | low | candidate | a module-level private symbol defined but never referenced (repo-wide) |

#### oop-composition (20)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-OOP-BUILDER-CLASS` | low | candidate | a stateful class with one `build`/`create` producer — use a factory classmethod |
| `PY-OOP-CLOSURE-CAPTURE` | suggestion | candidate | a thin inner closure capturing outer locals and passed around |
| `PY-OOP-CONSTRUCTOR-WALL` | low | candidate | a constructor call with many kwargs (threshold) — compose sub-models |
| `PY-OOP-DATACLASS-IN-PYDANTIC` | medium | auto | a `@dataclass` in a Pydantic project — use `BaseModel` |
| `PY-OOP-DICT-MUTATION-BUILDER` | suggestion | candidate | a function mutating a dict param in place and returning it (validators exempt) |
| `PY-OOP-DISPATCH-LADDER` | low | candidate | an if/elif (or guard-clause) ladder on one discriminator — use dispatch |
| `PY-OOP-DUPLICATE-BLOCK` | low | candidate | a duplicated statement block within a file — extract a helper |
| `PY-OOP-FIELD-COPY` | low | candidate | many `target.x = source.x` field copies — add a `from_*` classmethod |
| `PY-OOP-FLAT-FIELD-MODEL` | low | candidate | a `BaseModel` with many flat fields (threshold) — nest sub-models |
| `PY-OOP-FREE-FN-ORCHESTRATOR` | low | candidate | 3+ free functions threading one value (CLI modules exempt) — use a coordinator |
| `PY-OOP-GOD-CLASS` | low | candidate | a class over the method/attribute threshold — split responsibilities |
| `PY-OOP-HIGH-COMPLEXITY` | low | candidate | a function over the cyclomatic-complexity threshold |
| `PY-OOP-LONG-PARAM-LIST` | low | candidate | a function over the parameter-count threshold — bundle into an object |
| `PY-OOP-MODEL-REBUILD` | suggestion | candidate | a `model_rebuild()` call — confirm a real circular import exists |
| `PY-OOP-MODULE-CONST-FOR-SUBCLASS` | suggestion | candidate | module consts name-prefixed for a subclass — hoist to ClassVars |
| `PY-OOP-PARALLEL-SIBLING` | low | candidate | same-file functions with identical skeletons differing only in constants |
| `PY-OOP-STATIC-METHOD-CLASS` | low | candidate | a class of only `@staticmethod`s — use functions or real OOP |
| `PY-OOP-THIN-WRAPPER` | low | candidate | a function forwarding its args verbatim to one call |
| `PY-XFILE-DUP-FUNCTION` | low | candidate | a function sharing its shape with a clone in another file (CLI commands exempt) |
| `PY-XFILE-DUP-MODEL` | low | candidate | a model sharing its field-set with a clone in another file |

#### testing (9)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-TEST-DUPLICATE-SETUP` | low | candidate | a repeated arrange block across tests — extract a fixture |
| `PY-TEST-FIXTURE-MUTABLE-WIDE-SCOPE` | medium | candidate | a session/module/package fixture returning a mutable literal |
| `PY-TEST-LOGIC-IN-TEST` | low | candidate | `if`/`for`/`while`/`try` in a test body |
| `PY-TEST-NO-ASSERTION` | medium | candidate | a test that asserts nothing |
| `PY-TEST-OVER-MOCKING` | low | candidate | too many mocks in one test (threshold) |
| `PY-TEST-PARAMETRIZE-CANDIDATE` | medium | candidate | N near-identical tests differing only in literals — parametrize |
| `PY-TEST-SKIP-NO-REASON` | low | candidate | `skip`/`skipif`/`xfail` without `reason=` |
| `PY-TEST-SLEEP` | low | candidate | `time.sleep()` in a test |
| `PY-TEST-UNUSED-FIXTURE` | low | candidate | a fixture defined but never requested (repo-level) |

#### style (6)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `PY-STYLE-FILE-SIZE` | low | auto | a file over the line-count threshold — split into a package |
| `PY-STYLE-IF-FALSE-IMPORT` | low | auto | an import guarded by `if False:` instead of `TYPE_CHECKING` |
| `PY-STYLE-INLINE-IMPORT` | medium | auto | an import inside a function body — move to module top |
| `PY-STYLE-STALE-COMMENT` | low | candidate | a comment referencing a file path that no longer exists |
| `TS-STYLE-DUPLICATE-IMPORT` | low | auto | multiple separate imports from one module — merge them |
| `TS-STYLE-FILE-SIZE` | low | auto | a file over the line-count threshold — split it |

#### react (14)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `TS-REACT-ARRAY-INDEX-KEY` | medium | candidate | an array index used as a React `key` — reorder/insert bug |
| `TS-REACT-ASYNC-EFFECT` | medium | auto | an async function passed to `useEffect` (its Promise becomes the cleanup) |
| `TS-REACT-DEEP-JSX-NESTING` | low | candidate | JSX nested past the threshold — extract a sub-component |
| `TS-REACT-EAGER-STATE-INIT` | medium | candidate | `useState(expensiveCall())` re-run every render — use a lazy initializer |
| `TS-REACT-EXTRACTABLE-HELPER` | low | candidate | a pure helper nested in a component — lift to a module util |
| `TS-REACT-EXTRACTABLE-HOOK` | low | candidate | a large hook cluster in a component — extract a custom `use*` hook |
| `TS-REACT-MULTI-COMPONENT-FILE` | low | candidate | multiple components in one file — one per file |
| `TS-REACT-PARALLEL-SIBLING` | low | candidate | near-twin components/functions differing only in constants |
| `TS-REACT-RANDOM-KEY` | medium | auto | a freshly-generated `key` (`Math.random`/`Date.now`/`randomUUID`) — remounts |
| `TS-REACT-REPEATED-JSX` | low | candidate | repeated sibling JSX of the same shape — render from `.map()` |
| `TS-REACT-TOO-MANY-PROPS` | low | candidate | a component over the prop-count threshold — group into objects |
| `TS-XFILE-DUP-COMPONENT` | low | candidate | a component duplicated across files |
| `TS-XFILE-DUP-FUNCTION` | low | candidate | a function duplicated across files |
| `TS-XFILE-DUP-JSX-BLOCK` | low | candidate | a hand-rolled JSX sub-tree duplicated across files |

#### a11y (11)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `TS-A11Y-ANCHOR-NO-HREF` | medium | candidate | an `<a>` without `href` — not focusable or a real link |
| `TS-A11Y-AUTOFOCUS` | low | candidate | `autoFocus` — disorients screen-reader/keyboard users |
| `TS-A11Y-DECORATIVE-ICON` | low | candidate | a decorative icon beside text without `aria-hidden` |
| `TS-A11Y-FORM-LABEL` | medium | candidate | a form control with no associated label/`aria-label` |
| `TS-A11Y-ICON-BUTTON-NO-LABEL` | medium | candidate | an icon-only button with no accessible name |
| `TS-A11Y-IFRAME-TITLE` | medium | candidate | an `<iframe>` with no `title` |
| `TS-A11Y-IMG-NO-ALT` | medium | candidate | an `<img>` with no `alt` |
| `TS-A11Y-MOUSE-NO-KEY` | medium | candidate | `onMouseOver`/`onMouseOut` with no `onFocus`/`onBlur` equivalent |
| `TS-A11Y-NONINTERACTIVE-ONCLICK` | medium | candidate | an `onClick` on a non-interactive element with no role/keyboard support |
| `TS-A11Y-POSITIVE-TABINDEX` | medium | candidate | a positive `tabIndex` overriding natural tab order |
| `TS-A11Y-REDUNDANT-ROLE` | low | candidate | a `role` restating the element's implicit ARIA role |

#### design-system (3)

| rule_id | severity | verdict | what it flags |
|---|---|---|---|
| `TS-DS-DIRECT-UI-IMPORT` | medium | candidate | a direct import from the raw UI layer — use the design-system shell |
| `TS-DS-INLINE-PRIMITIVE` | low | candidate | inline markup matching a declared primitive — use the component |
| `TS-DS-SIZE-OVERRIDE` | low | candidate | a primitive sized via `className` — use its `size` prop |

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
`ignore_list`, `ignore_remove`, `finding_detail`. The MCP `scan`
takes `severity` and `since` (audit only a branch's changes), so an agent reviewing a PR pulls
back just the changed files' findings — fewer tokens, same cross-file correctness.

### MCP output format

`scan` and `report` default to a **compact** payload to save tokens:

- A top-level `rules` map (`rule_id → {category, verdict_kind, checklist_item, standard_refs, suggestion}`) is emitted once.
- Each finding is a slim object `{rule_id, severity, line, message}` — per-finding `evidence` and repeated rule metadata are dropped.
- Per-file objects keep `file`/`role`/`counts`/`findings`; the low-signal `language`/`cached`/`suppressed`/`ignored`/`skipped_rules` fields are omitted (`suppressed`/`ignored` remain in the top-level `totals`).

Control the shape with the `detail` parameter:

| `detail` | shape |
|---|---|
| `"compact"` *(default)* | hoisted `rules` map + slim findings (no `evidence`) |
| `"full"` | legacy inline shape — every field on every finding, `evidence` included |
| `"summary"` | counts only: `{totals, by_rule, by_file}` — no individual findings |

To fetch the full record for one finding (including `evidence` and `suggestion`), call
`finding_detail(file, rule_id, line)`. This is the recovery path when you need details
that compact mode drops.

> The CLI (`auditr scan -f json`) is **unaffected** — its JSON output is unchanged.

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
uv run pytest            # 1365 tests
uv run pytest --cov=auditor
uv run ruff check auditor tests && uv run ruff format --check auditor tests
```

The package is held to its own standard: registries and analyzers are classes, config is
typed `pydantic-settings`, the index is async (in-house worker thread, no third-party
driver), and `auditor scan auditor/` on its own source is clean apart from a few
intentional, explainable findings (worker-thread `BaseException` propagation, plugin-load
isolation).
