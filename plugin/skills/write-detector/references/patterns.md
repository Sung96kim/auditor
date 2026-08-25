# plugin/skills/write-detector/references/patterns.md
# Detector shapes, the severity/verdict decision, and testing

Grounded against the installed `auditor` package (`auditr version` 0.8.0 at the time this was
written) — re-check `auditr rules list` / `auditr plugins list` if anything here looks stale.

## Three shapes

Every built-in detector is one of these three. Pick the narrowest one that covers your rule.

### 1. AST-walk (single file) — the default

Walks `ctx.tree` and flags a syntactic pattern, no context beyond the current file. This is
`template.py`: a bare `except:` is just `isinstance(node, ast.ExceptHandler) and node.type is
None`. Most rules are this shape — reach for it first.

```python
def run(self, ctx: AuditContext) -> list[Finding]:
    out: list[Finding] = []
    for node in ast.walk(ctx.tree):
        if <pattern matches node>:
            out.append(self.make_finding(ctx, line=node.lineno, message="..."))
    return out
```

### 2. Framework/config-gated

Two extra guards before the pattern check: (a) only run on files that actually use the
framework, so a generic name never false-positives in unrelated code, and (b) only run when the
project has declared a fact the file itself can't reveal (e.g. "sessions are async" — that lives
in a factory elsewhere, not in the model file). Real example, `SA-IMPLICIT-LAZY-ASYNC`
(`auditor/languages/python/detectors/sqlalchemy_rules.py`):

```python
class SqlAlchemyRule(Detector):
    """Per-file SQLAlchemy rule: gated to files importing sqlalchemy."""
    abstract: ClassVar[bool] = True
    framework: ClassVar[str | None] = "sqlalchemy"

    def run(self, ctx: AuditContext) -> list[Finding]:
        if not imports_module(ctx.tree, "sqlalchemy"):
            return []
        return self.check(ctx, _sa_from_aliases(ctx.tree))

class ImplicitLazyAsync(SqlAlchemyRule):
    rule_id: ClassVar[str] = "SA-IMPLICIT-LAZY-ASYNC"

    def check(self, ctx: AuditContext, aliases: dict[str, str]) -> list[Finding]:
        if not ctx.config.settings.sqlalchemy.async_session:
            return []  # dormant unless the project declares async sessions
        # ... walk ctx.tree for relationship() calls with no explicit lazy= ...
```

The `[tool.auditor.sqlalchemy] async_session = true` fact lives in the *scanning* repo's config,
not in a plugin — a plugin author gets the same lever by reading `ctx.config.settings` for any
declared config the base `AuditorSettings` model already exposes (`ctx.project_deps` is the
zero-config version of the same idea: `"pydantic" not in ctx.project_deps` gates a rule to
projects that actually depend on the framework, no config declaration needed).

### 3. Cross-file-resolving

Still a per-file detector (`run(self, ctx)` — one file in, one file's findings out), but it
follows a call to its definition **in another file** via `ctx.resolver`
(`auditor/languages/python/resolve.py`, `CalleeResolver.resolve_func`). This is how a detector
reasons about what a helper function *does* without the caller re-implementing that logic. Real
example, abridged from `_add_resolved_freshen` in `sqlalchemy_rules.py`:

```python
def run(self, ctx: AuditContext) -> list[Finding]:
    resolver = getattr(ctx, "resolver", None)
    if resolver is None:
        return []  # no resolver set up for this scan (e.g. --isolated) — degrade to no-op
    out: list[Finding] = []
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        callee = resolver.resolve_func(node, ctx.tree)  # the callee's ast.FunctionDef, or None
        if callee is None:
            continue  # honest unknown — couldn't statically resolve it, don't guess
        if <callee's body matches something you care about>:
            out.append(self.make_finding(ctx, line=node.lineno, message="..."))
    return out
```

`resolve_func` only resolves repo-local modules by default; it follows into an installed
dependency's source only if the project lists that package's dotted-name prefix under
`[tool.auditor] resolve_packages`. An unresolvable call (dynamic dispatch, `**kwargs` forwarding,
outside `resolve_packages`) returns `None` — always treat that as "don't know," never as "assume
it's fine" or "assume it's a problem."

**This is not the same thing as a true repo-level/cross-file pass** (`GRAPH-*`, `PY-XFILE-DUP-*`,
`PY-DEAD-SYMBOL`) — those are computed by dedicated internal passes over the whole index
(`auditor/graph/detectors.py`, `auditor/dead_code.py`, the crossfile dedup pass) and marked
`repo_level: ClassVar[bool] = True`, which makes the engine skip their per-file `run` entirely
(`auditor/engine.py`, `_partition_rules`: `if det.repo_level: continue`). There is no plugin hook
into those passes — `ctx.resolver` is the actual ceiling for how far a local plugin can see
beyond the current file.

## Choosing `default_severity`

`Severity`, most to least severe: `blocking > high > medium > low > suggestion` (`suggestion` is
a nudge, never CI-blocking). Match the built-in rules' calibration rather than guessing:

- **blocking** — the unambiguous, no-legitimate-use patterns: `AV-MAL-MATCH`,
  `PY-MAL-OBFUSCATED-EXEC`, `CFG-ENV-FILE-COMMITTED`, `PY-SEC-DANGEROUS-EVAL`.
- **high** — a real, common security/correctness risk that still needs the surrounding context
  to be sure: `PY-SEC-SHELL-INJECTION`, `CFG-SECRET-DETECTED`, `AV-MAL-HEURISTIC`.
- **medium** — a real but narrower or more situational risk: `PY-SEC-FLASK-DEBUG`,
  `PY-CORRECT-BROAD-EXCEPT`.
- **low** — a real but low-stakes issue, mostly hygiene: `PY-TYPING-MISSING-HINTS`,
  `PY-SEC-BIND-ALL-INTERFACES`.
- **suggestion** — below the CI-blocking floor entirely; a nudge you'd want surfaced but never
  fail a build over: `GRAPH-GOD-CONCEPT`, `GRAPH-NAMING-INCONSISTENCY`.

Remember severity is a **default** — a repo can override it per-rule in config
(`[tool.auditor.rules] LOCAL-... = { severity = "high" }`), so pick the severity that's honest
for a generic repo, not tuned to any one project's risk tolerance.

## Choosing `verdict_kind`: `auto` vs `candidate`

This is the decision that matters most — it decides whether your rule can ever gate CI
(`scan --fail-on <severity>` only counts `auto` findings; `candidate` never trips the gate) and
whether an agent re-judges every hit before acting on it (`judge-findings` skill).

- **`auto`** — the tool can decide by itself, no domain judgment required. The pattern is either
  definitionally correct (a bare `except:` is *always* worth flagging — there's no legitimate
  "type is None" you'd want to allow) or the detector has already filtered out the ambiguous
  cases in code (e.g. `PY-SEC-BIND-ALL-INTERFACES` only fires on a literal `0.0.0.0` bind, not a
  variable that *might* resolve to one). Real `auto` rules span every severity: `blocking`
  (`AV-MAL-MATCH`, `CFG-ENV-FILE-COMMITTED`), `high` (`PY-SEC-HARDCODED-SECRET`,
  `PY-SEC-SHELL-INJECTION`), `medium` (`PY-CORRECT-BROAD-EXCEPT`, `PY-SEC-FLASK-DEBUG`), `low`
  (`PY-TYPING-MISSING-HINTS`, `PY-SEC-BIND-ALL-INTERFACES`).
- **`candidate`** — evidence, not a verdict; a human/agent judgment call decides fix vs.
  false-positive. Use this whenever the same syntactic shape has a legitimate reading depending on
  context the detector can't see: reachability from untrusted input (`PY-SEC-SSRF`), whether a
  swallowed exception is a deliberate best-effort path or a hidden bug
  (`PY-CORRECT-SWALLOWED-EXCEPTION`), whether a flat-field model is a wire-boundary passthrough
  or genuine feature-envy (`PY-OOP-FLAT-FIELD-MODEL`), or anything measured against a *tunable*
  threshold rather than a bright line (`PY-OOP-HIGH-COMPLEXITY`, every `threshold.*` knob).

Rule of thumb: if you can picture a real, non-contrived snippet where firing would be *wrong* and
someone would reasonably want to keep the code as-is, it's `candidate`. If every instance of the
pattern is worth acting on regardless of surrounding context, it's `auto`. When genuinely unsure,
default to `candidate` — a false-positive `auto` finding blocks CI for the wrong reason; a
false-positive `candidate` finding just costs one judgment call.

## Testing a detector

New production code needs a test (repo-wide rule, not special to detectors). The detector-level
test doesn't need the full CLI/config machinery — construct a minimal `AuditContext` directly and
call `.run()`, same as the engine does internally, just without the config-loading ceremony:

```python
# tests/test_local_no_bare_except.py
import ast
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from auditor.config import AuditorSettings, ResolvedConfig
from auditor.languages.base import AuditContext
from auditor.models import FileRole

_PLUGIN = Path(__file__).parents[1] / ".auditor" / "plugins" / "local_no_bare_except.py"
_FIXTURES = Path(__file__).parent / "fixtures"


def _load_detector():
    spec = spec_from_file_location("local_no_bare_except", _PLUGIN)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ExampleNoBareExcept()


_DETECTOR = _load_detector()  # import once — re-importing re-registers the rule_id and raises


def _run(fixture_name: str):
    source = (_FIXTURES / fixture_name).read_text()
    tree = ast.parse(source)
    settings = AuditorSettings()
    config = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path=fixture_name)
    ctx = AuditContext(
        file_path=fixture_name, source=source, tree=tree, role=FileRole.PRODUCTION, config=config
    )
    return _DETECTOR.run(ctx)


def test_flags_bare_except_on_the_except_line():
    findings = _run("local_no_bare_except_bad.py")
    assert [(f.rule_id, f.line) for f in findings] == [("LOCAL-NO-BARE-EXCEPT", 4)]


def test_clean_fixture_yields_nothing():
    assert _run("local_no_bare_except_good.py") == []
```

Load the detector module **once** at module scope, not per test — `Detector.__init_subclass__`
registers `rule_id` in the process-global registry the instant the class body executes, so
re-executing the plugin file a second time (e.g. inside a per-test helper) raises `duplicate
rule_id`, not a fresh class. Verified: written the naive per-test-reimport way, this fails on the
second test with exactly that error; hoisting the load to module scope (`_DETECTOR = ...` above)
fixes it.

`AuditorSettings()` with no arguments is fine here — you're unit-testing the detector's *own*
logic (`.run()` returns raw `default_severity`/`verdict_kind`, unmodified), not the config-driven
severity/verdict overrides that only apply during a real `scan` (`ResolvedConfig.effective`,
applied once per file by `LanguageAuditor._collect`). If your rule is config-gated (shape 2
above), set the relevant field on `AuditorSettings(...)` directly in the test instead of the
default.

## Worked end-to-end

Verified against this repo (`auditr` 0.8.0). `template.py` (this skill dir) copied verbatim into
`.auditor/plugins/local_no_bare_except.py`:

```python
class ExampleNoBareExcept(Detector):
    rule_id: ClassVar[str] = "LOCAL-NO-BARE-EXCEPT"
    category: ClassVar[Category | str] = Category.CORRECTNESS
    default_severity: ClassVar[Severity] = Severity.HIGH
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.AUTO

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                out.append(
                    self.make_finding(ctx, line=node.lineno, message="bare `except:` — catch a specific exception")
                )
        return out
```

Fixture with the violation:

```python
def handle():
    try:
        risky()
    except:
        pass
```

The unit test above passes. Then the real trust-gated CLI check —
`auditr scan local_no_bare_except_bad.py -a -f json --isolated` — returns (trimmed to the new
rule):

```json
{
  "rule_id": "LOCAL-NO-BARE-EXCEPT",
  "category": "correctness",
  "severity": "high",
  "verdict_kind": "auto",
  "line": 4,
  "message": "bare `except:` — catch a specific exception",
  "evidence": "except:"
}
```

Drop `-a` (or set `trust_local_plugins = true`) and the finding disappears — `auditr plugins
list` won't show `local_no_bare_except.py` either, and a warning explains why (untrusted local
plugin, ignored). Running the same scan on a clean fixture (`except ValueError:` instead of bare
`except:`) returns no `LOCAL-NO-BARE-EXCEPT` finding, confirming the clean-fixture side too.
