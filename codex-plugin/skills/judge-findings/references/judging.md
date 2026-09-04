# Per-category verdict heuristics

For every `candidate` finding you have three verdicts. Pick one:

- **fix** — it's a real issue; change the code.
- **skip-directive** — it's a genuine false positive that will keep re-firing on this exact
  line/file (the pattern is inherent to the code, not a one-off); suppress it permanently with
  `# auditor: skip: <RULE-ID>` on the finding's reported `line`. See the SKILL body for the
  line-anchoring footgun before you place one.
- **dismiss** — it's a genuine false positive, but not worth a permanent marker (a one-off,
  code that's about to change anyway, or a borderline call you want on record without editing
  the file). State the reason in your verdict summary; no code change.

Never silently drop a candidate. If you didn't open `file:line` and read the evidence, you
haven't judged it — you've guessed.

This file covers every category that actually emits `candidate` findings (checked against
`auditr rules list`). `typing` (`PY-TYPING-MISSING-HINTS`, `PY-TYPING-UNTYPED-DICT`) and
`secrets` (`CFG-SECRET-DETECTED`, `CFG-ENV-FILE-COMMITTED`) are **auto-verdict only** in this
build — the tool already decided them deterministically, so report them as-is; don't re-litigate.

## security

Rules: `PY-SEC-ASSERT-FOR-SECURITY`, `PY-SEC-DJANGO-RAW-SQL`, `PY-SEC-INSECURE-RANDOM`,
`PY-SEC-PATH-TRAVERSAL`, `PY-SEC-SQL-STRING-BUILD`, `PY-SEC-SSRF`, `SA-RAW-SQL`,
`TS-SEC-DANGEROUS-HTML`.

- **Real issue**: the sink is actually reachable from untrusted input — a network request body,
  query param, uploaded file, or a DB row that itself originated from user input — with no
  parameterization/allow-list/escaping between the source and the sink.
- **False positive**: most of these detectors already filter constants (e.g. `PY-SEC-SSRF` only
  fires when the URL is caller-derived, not a literal) — so if a candidate still looks wrong,
  check *why* the "caller" isn't actually untrusted: internal service-to-service call, a value
  already validated/parameterized upstream, or test/fixture code exercising the pattern on
  purpose.
- **Verify**: trace the flagged value backward to its origin. For `SQL-STRING-BUILD`/`RAW-SQL`,
  confirm it reaches `.execute()`/`.raw()` unparameterized. For `SSRF`, confirm there's no
  allow-list downstream.
- **Decision**: reachable + unguarded → fix (parameterize, allow-list, escape). Provably
  trusted/internal origin → dismiss with reason. Deliberate vulnerable-pattern-as-test-fixture →
  skip-directive.

## malware

Rules: `AV-MAL-HEURISTIC`, `PY-MAL-CREDENTIAL-ACCESS`, `PY-MAL-DYNAMIC-IMPORT`,
`PY-MAL-ENCODED-BLOB`, `PY-MAL-EXFIL-URL`, `PY-MAL-PICKLE-REDUCE`, `SH-MAL-*`, `TS-MAL-*`.

The unambiguous malware patterns (decode→exec, fetch→exec, reverse-shell wiring) are
**auto**-verdict and blocking — you don't judge those. The candidates here are the single-signal
string sweeps (`malware_sweeps.py`): a base64-ish blob, a known secret/credential path, an
anonymous paste/tunnel/webhook URL, a dynamic import from a decoded name, a pickle
`__reduce__` gadget. Each is one signal without the combining pattern that would make it
auto-blocking, so false-positive risk is real: legit test fixtures with base64 sample data,
docs/tests that reference `~/.ssh` or `~/.aws` paths without touching them, dev tools that use
tunnel URLs (ngrok, etc.) on purpose, plugin systems with legitimate dynamic imports.
- **Verify**: read the surrounding function, not just the line — is this test/fixture data, or
  runtime logic? Does the credential-path read actually flow anywhere (especially outbound)? Is
  there a nearby decode-then-exec or fetch-then-exec that would make this auto-blocking instead?
- **Decision**: this category is security-sensitive — false negatives are expensive, so don't
  dismiss on a hunch. Demonstrably malicious → do not quietly "fix" it as a style issue; flag it
  loudly and prefer removal over patching. Demonstrably legitimate (test fixture, doc example,
  intentional tool behavior) → skip-directive scoped to the exact line, with a short comment
  explaining why. When you can't tell → dismiss with reason and say so explicitly; don't guess.

## supply-chain

Rules: `DEP-VULN-KNOWN`, `MF-SUPPLY-INSTALL-HOOK`, `PY-SUPPLY-SETUP-EXEC`.

- **Real issue**: an install-time hook (`setup.py` `cmdclass`, a packaging script) actually
  performs a network fetch, arbitrary command execution, or runs obfuscated/decoded content
  during `pip install`/`npm install` — code that runs on every consumer's machine, unreviewed.
- **False positive**: a legitimate build step with no network/exec of remote content (compiling
  an extension, stamping a version file). For `DEP-VULN-KNOWN`, the CVE's vulnerable code path
  may not be reachable from how this project actually uses the dependency.
- **Verify**: read exactly what the hook does — network calls? exec of downloaded/decoded
  content? For `DEP-VULN-KNOWN`, check the advisory against the actual usage.
- **Decision**: real install-time risk → fix (remove the hook, or pin/patch the dependency).
  Necessary, side-effect-free build step or confirmed-unreachable CVE path → dismiss with
  reason. Don't skip-suppress this category casually — supply-chain detection here is
  deliberately narrow/high-signal by design, so a candidate earned real scrutiny to get flagged.

## oop-composition

The largest bucket (24 candidates): `PY-OOP-CONSTRUCTOR-WALL`, `PY-OOP-FLAT-FIELD-MODEL`,
`PY-OOP-THIN-WRAPPER`, `PY-OOP-BUILDER-CLASS`, `PY-OOP-DISPATCH-LADDER`,
`PY-OOP-STATIC-METHOD-CLASS`, `PY-OOP-LONG-PARAM-LIST`, `PY-OOP-GOD-CLASS`,
`PY-OOP-HIGH-COMPLEXITY`, `PY-OOP-CLOSURE-CAPTURE`, `PY-OOP-DICT-MUTATION-BUILDER`,
`PY-OOP-DUPLICATE-BLOCK`, `PY-OOP-FIELD-COPY`, `PY-OOP-FREE-FN-ORCHESTRATOR`,
`PY-OOP-LOGIC-IN-CLI`, `PY-OOP-MODEL-REBUILD`, `PY-OOP-MODULE-CONST-FOR-SUBCLASS`,
`PY-OOP-PARALLEL-SIBLING`, `PY-OOP-TWIN-METHODS`, `PY-XFILE-DUP-FUNCTION`,
`PY-XFILE-DUP-MODEL`, `PY-XFILE-PRIVATE-USED`, `GRAPH-GOD-CONCEPT`,
`GRAPH-SCATTERED-CONCEPT`.

- **Real issue vs false positive**: is this genuine feature-envy or a missing seam — repeated
  shape across the codebase that's crying out for a class/composition — or is it a legitimate
  boundary? A flat-field model that mirrors an external API/DB row at a wire boundary is
  *supposed* to be flat. A thin wrapper that exists to satisfy an ABC/interface contract is
  deliberate, not drift. A "god class" that's a single cohesive orchestrator by design (one
  entry point, clearly scoped) isn't the same as one that grew by accretion.
- **Verify**: for anything cross-file (`PY-XFILE-DUP-*`, `PY-XFILE-PRIVATE-USED`) or any
  delete/merge call, run `graph usages <symbol>` (MCP `graph_usages` or CLI `auditr graph
  usages`) and read `used_by` before acting — don't trust a single file's view. For
  flat-field/model complaints, check whether the model sits at a wire/API/DB boundary (a
  Pydantic passthrough) vs an internal domain object that should carry behavior.
- **Decision**: real internal-domain duplication or feature-envy → fix, following the rule's
  `suggestion` (extract a class, add a `from_*` classmethod, compose instead of copy). Legitimate
  boundary type, ABC conformance, or deliberate simplicity → dismiss with reason. A pattern the
  team has already decided against elsewhere in this exact form → skip-directive.

## dead-code

Rule: `PY-DEAD-SYMBOL` (repo-level, computed by the crossfile pass — a private module-level
symbol with zero references anywhere in the index).

- **Real issue**: the symbol genuinely has no references anywhere in the repo.
- **False positive**: the symbol is a public API entry point consumed by something outside this
  repo's graph (the index is repo-partitioned — cross-repo usage isn't visible), or it's reached
  dynamically (string-based dispatch, decorator registration, `getattr`, `__all__` exports)
  which static analysis won't see, or it's a fixture kept intentionally for future use.
- **Verify — mandatory before deleting anything**: `graph usages <symbol>` and confirm `used_by`
  is genuinely empty. Also grep for the symbol name as a string literal (dynamic dispatch,
  plugin registries) — the graph only sees static references.
- **Decision**: confirmed zero usages, not a public/plugin entrypoint → fix (delete). Any doubt
  at all → dismiss with reason; do not delete on suspicion.

## async

Rules: `PY-ASYNC-NO-AWAIT-BODY`, `PY-ASYNC-SEQUENTIAL-AWAITS`, `PY-ASYNC-SYNC-IO`,
`PY-ASYNC-UNLOCKED-LAZY-INIT`, `SA-ASYNC-EXPIRE-ON-COMMIT`, `SA-GREENLET-ATTR-AFTER-COMMIT`,
`SA-IMPLICIT-LAZY-ASYNC`.

- **Real issue**: an actual blocking call inside an `async def` that runs on the shared event
  loop (`SYNC-IO`); a genuine unsynchronized check-then-set race on a lazily-initialized
  attribute reachable from concurrent callers (`UNLOCKED-LAZY-INIT`); independent `await`s inside
  a loop that could run concurrently instead of serially (`SEQUENTIAL-AWAITS`).
- **False positive**: the "sync" call is fast/in-memory, not real I/O; the async function is
  intentionally sync-bodied because it overrides an interface contract that mandates `async def`
  (`NO-AWAIT-BODY`); the loop's awaits are *not* independent — each genuinely depends on the
  previous result, so serial execution is correct.
- **Verify**: trace whether the loop body's iterations share state or must run in order; check
  whether the lazy-init attribute is actually reachable from more than one concurrent task (a
  single-consumer async path has no race regardless of the missing lock).
- **Decision**: real blocking call / real race / genuinely-independent-but-serial awaits → fix
  (`asyncio.to_thread`, add a lock, `asyncio.gather`). Interface-mandated shape or a real
  ordering dependency → dismiss with reason.

## correctness

Rules: `PY-CORRECT-NAIVE-DATETIME`, `PY-CORRECT-RAISE-WITHOUT-FROM`,
`PY-CORRECT-SWALLOWED-EXCEPTION`, `PY-PYDANTIC-V1-CONFIG-CLASS`, `SA-LAZY-DYNAMIC`,
`SA-MUTABLE-DEFAULT`, `SA-NAIVE-DATETIME-DEFAULT`.

- **Real issue**: the swallowed exception hides a failure a caller genuinely needs to know
  about; the naive datetime crosses a timezone boundary somewhere; `class Config:` on a
  pydantic v2 `BaseModel` silently drops a setting the author actually intended to apply (v2
  keeps the inner class as a deprecated shim but never validates its keys).
- **False positive**: the swallow is a deliberate, already-documented best-effort path (e.g. an
  optional cache write that must not break the caller on a read-only filesystem — see
  `references/examples.md` for a real one); the naive datetime never crosses a timezone
  boundary; every key under `class Config:` is a pure legacy no-op with no v2 equivalent.
- **Verify**: read the `except` block's existing comment/context first — is "nothing to do here"
  already reasoned out? For `PY-PYDANTIC-V1-CONFIG-CLASS`, check whether any configured key
  (e.g. `orm_mode`) has a real v2 replacement (`from_attributes`) that's now silently unapplied.
- **Decision**: a real setting silently dropped, or a real failure hidden from a caller that
  needs it → fix. Deliberate, already-reasoned best-effort behavior → skip-directive with a
  short comment, or dismiss if the code changes often enough that a directive would just be
  noise.

## config

Rules: `PY-CONFIG-IMPORT-TIME-IO`, `PY-CONFIG-SCATTERED-SETTINGS`.

- **Real issue**: module import performs real side-effectful I/O (network call, a file read that
  can fail) — making import order and environment-dependent behavior fragile; a
  `BaseSettings` subclass lives outside the project's designated settings home, fragmenting
  config discovery.
- **False positive**: the "import-time IO" reads a bundled, always-present static resource with
  no failure mode across environments; the "scattered" settings class is deliberately scoped to
  a self-contained subpackage/plugin that's meant to own its own config.
- **Verify**: check whether the import-time call can fail, block, or behave differently between
  test/prod; check whether the settings class is meant to be central vs intentionally local.
- **Decision**: fragile or side-effectful import → fix (lazy-load it). Legitimately scoped/local
  → dismiss with reason.

## testing

Rules: `PY-TEST-DUPLICATE-SETUP`, `PY-TEST-FIXTURE-MUTABLE-WIDE-SCOPE`,
`PY-TEST-LOGIC-IN-TEST`, `PY-TEST-NO-ASSERTION`, `PY-TEST-OVER-MOCKING`,
`PY-TEST-PARAMETRIZE-CANDIDATE`, `PY-TEST-SKIP-NO-REASON`, `PY-TEST-SLEEP`,
`PY-TEST-UNUSED-FIXTURE`.

- **Real issue**: a test with genuinely no assertion can't fail no matter what the code does; a
  wide-scoped (module/session) fixture returning a mutable literal genuinely leaks state between
  tests that mutate it; `time.sleep()` genuinely makes the test flaky/slow instead of
  polling/awaiting the real condition.
- **False positive**: "no assertion" often means the assertion is shaped as a context manager
  (`with pytest.raises(...):`) the detector's AST walk didn't recognize as this test's
  assertion — open the function body and check directly. "Over-mocking" can reflect a test
  correctly isolating a unit with many real collaborators, where each mock legitimately stands
  in for a boundary — not testing mocks for their own sake.
- **Verify**: always open the test body directly; don't trust the message's shape assumption.
  Count what's actually asserted/mocked and why.
- **Decision**: genuinely weak test → fix (add the assertion, extract the fixture, replace sleep
  with a poll/await, parametrize). Legitimate shape the heuristic under-recognizes → dismiss
  with reason.

## react

Rules: `TS-REACT-ARRAY-INDEX-KEY`, `TS-REACT-DEEP-JSX-NESTING`, `TS-REACT-EAGER-STATE-INIT`,
`TS-REACT-EXTRACTABLE-HELPER`, `TS-REACT-EXTRACTABLE-HOOK`, `TS-REACT-MULTI-COMPONENT-FILE`,
`TS-REACT-PARALLEL-SIBLING`, `TS-REACT-REPEATED-JSX`, `TS-REACT-TOO-MANY-PROPS`,
`TS-XFILE-DUP-COMPONENT`, `TS-XFILE-DUP-FUNCTION`, `TS-XFILE-DUP-JSX-BLOCK`.

- **Real issue**: an array index used as `key` on a list that can actually reorder, filter, or
  insert (real remount/state-loss risk); N sibling JSX blocks that genuinely repeat the same
  shape driven only by data (should be a `.map()`).
- **False positive**: the array-index key is on a list that's provably static in this render
  path (never sorted/filtered/inserted); "repeated" JSX blocks that look similar but diverge in
  real per-item behavior, not just literal values.
- **Verify**: check whether the list can reorder (sort/filter calls upstream, user-driven
  reordering); read each "repeated" block for behavioral differences beyond substituted data.
- **Decision**: real reorder risk or real data-driven repetition → fix (stable key from data,
  extract + `.map()`). Static list or meaningfully different blocks → dismiss with reason.

## a11y

Rules: `TS-A11Y-ANCHOR-NO-HREF`, `TS-A11Y-AUTOFOCUS`, `TS-A11Y-DECORATIVE-ICON`,
`TS-A11Y-FORM-LABEL`, `TS-A11Y-ICON-BUTTON-NO-LABEL`, `TS-A11Y-IFRAME-TITLE`,
`TS-A11Y-IMG-NO-ALT`, `TS-A11Y-MOUSE-NO-KEY`, `TS-A11Y-NONINTERACTIVE-ONCLICK`,
`TS-A11Y-POSITIVE-TABINDEX`, `TS-A11Y-REDUNDANT-ROLE`.

- **Real issue**: an interactive control genuinely has no keyboard or screen-reader path — an
  icon-only button with no accessible name, a `<div onClick>` with no role/tabIndex/keydown
  handler.
- **False positive**: an adjacent visible text label already satisfies the accessible-name
  requirement in a way the AST scan didn't connect to the control; "decorative icon" already has
  `aria-hidden` applied via a prop spread the static check can't see through.
- **Verify**: check the actual accessible-name path (`aria-label`, a visually-hidden text
  sibling, spread props) before assuming the finding is right.
- **Decision**: no real accessible path exists → fix (add the label/handler/alt/title). An
  accessible path is provably present via a pattern the detector missed → dismiss with reason.

## design-system

Rules: `TS-DS-DIRECT-UI-IMPORT`, `TS-DS-INLINE-PRIMITIVE`, `TS-DS-SIZE-OVERRIDE`.

- **Real issue**: a component bypasses an existing design-system shell/primitive when a matching
  one already covers the case — risking visual drift from the rest of the app.
- **False positive**: the raw import/inline markup lives inside the design-system package's own
  implementation, or it's a genuinely one-off case with no existing primitive that fits.
- **Verify**: confirm a matching primitive actually exists and covers this exact case — check
  the design-system package directly, don't assume from the rule firing.
- **Decision**: a matching primitive exists → fix (swap it in). No real primitive fits → dismiss
  with reason (a candidate for a *new* primitive is a separate task, not this judgment pass).

## style

Rules: `GRAPH-NAMING-INCONSISTENCY`, `PY-STYLE-LONG-COMMENT`, `PY-STYLE-STALE-COMMENT`,
`SH-STYLE-LONG-COMMENT`.

- **Real issue**: a comment references a symbol/path that's actually gone from the repo; a
  naming inconsistency reflects real repo-wide drift a reader would trip over.
- **False positive**: the "stale" reference is to an external doc/URL/generic term, not a repo
  symbol, so there's nothing to have gone stale; the "inconsistent" names actually refer to two
  distinct concepts that only look similar.
- **Verify**: for stale-comment, grep the repo for the referenced name/path yourself before
  trusting the rule's string match.
- **Decision**: genuinely stale or inconsistent → fix (update the comment, rename). False match
  on the string heuristic → dismiss with reason, or skip-directive if it will keep re-firing.
