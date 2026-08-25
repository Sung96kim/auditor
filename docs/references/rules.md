# rules reference

`auditr rules list` prints the catalogue of registered detector rules: rule id, category,
framework, default severity, verdict kind, standard references, and registration source. It loads
the target repo's config and plugins first, so plugin rules are in the catalogue too.
`auditr rules list --help` lists every flag. The command is the catalogue, so this page describes
how to read and filter it rather than repeating it.

## Common invocations

```bash
# the whole catalogue
auditr rules list

# one category (the error message on a typo lists the valid names)
auditr rules list -c security

# rules that cite a standard: bandit or owasp
auditr rules list -s bandit

# rules specific to one framework, e.g. pytest, sqlalchemy, pydantic
auditr rules list -f pytest

# filters combine
auditr rules list -c security -s owasp

# another checkout, so its plugin rules are the ones listed
auditr rules list -r ../other-repo

# raw JSON
auditr rules list --json
```

## What a row carries

- `rule_id`: stable identifier, e.g. `PY-SEC-WEAK-HASH`. It is what config, `--rule`, ignores,
  and skip directives address.
- `category`: which family the rule belongs to (below).
- `framework`: the framework the rule is specific to, or empty for framework-agnostic rules. It
  is descriptive and filterable; nothing gates on it automatically.
- `default_severity`: the severity before the repo's config adjusts it.
- `verdict_kind`: `auto` or `candidate` (below).
- `standard_refs`: external citations such as `bandit:B602` or `owasp:A03`.
- `source`: where the class was registered from. Built-in rules read `built-in`; a plugin rule
  names the module or file that was imported, so the column separates the two.

## Categories

Built-in categories, one clause each:

- `security`: injection, unsafe deserialization, weak crypto, and TLS or debug-mode mistakes.
- `malware`: payload shapes such as obfuscated exec, reverse shells, miners and exfil URLs, plus
  the ClamAV backend matches.
- `supply-chain`: install-time code execution in a package manifest, plus the osv-scanner package
  advisories.
- `secrets`: a committed credential, or a dotenv file tracked by the repo.
- `correctness`: bugs that are wrong regardless of style, such as swallowed exceptions, naive
  datetimes and SQLAlchemy misuse.
- `typing`: missing annotations and untyped dict returns.
- `async`: event-loop hazards such as sync I/O, dangling tasks, unawaited coroutines and lazy
  loads across a greenlet boundary.
- `config`: environment read ad hoc, I/O at import time, settings classes scattered across
  modules.
- `oop-composition`: shape and decomposition, such as constructor walls, god classes and concepts,
  dispatch ladders, duplicated blocks and private symbols used from another module.
- `style`: file size, comment blocks, import placement, and inconsistent naming of one concept.
- `react`: hook and component hazards such as async effects, array-index keys and extractable
  helpers, plus components and JSX duplicated across files.
- `a11y`: accessible name, keyboard reachability and role mistakes in JSX.
- `design-system`: raw markup where the repo's declared primitives or shell entrypoint belong.
- `testing`: test quality, such as an assertion-free test, over-mocking, sleeps and unused
  fixtures.
- `dead-code`: a module-level symbol defined but never referenced anywhere in the repo.

Reading and filtering them:

- A plugin may register a category string of its own; config validates against the union.
- `correctness`, `async`, `config`, and `typing` are Python-only. `security`, `malware`,
  `secrets`, and `supply-chain` span the languages where they apply.
- `-c` with an unknown name exits non-zero and prints the valid names, which is the cheapest way
  to see the live list.
- Whether a category runs at all is a config decision, not a catalogue one: `base` ships
  `oop-composition` off, `strict` turns it on. See [configuration.md](configuration.md).

## Verdicts and severity

- `auto` means the tool decided deterministically. Those findings gate CI.
- `candidate` means the rule found evidence and a reviewer has to judge it. Candidates never trip
  `scan --fail-on`, whatever their severity.
- Severity tiers, most severe first: `blocking`, `high`, `medium`, `low`, `suggestion`.
  `suggestion` sits below `low` and is for optional nudges.
- `scan -s`/`--severity` filters to exact tiers, `-m`/`--min-severity` to a tier and above, and
  `--fail-on` gates at a tier and above; see [scan.md](scan.md).
- A repo can override any rule's severity and enablement, and any threshold that drives it. The
  fields are in [configuration.md](configuration.md).

## Plugin rules

- `rules list` loads the repo's config and its trusted plugins before listing, so a rule that only
  ships in `.auditor/plugins/` shows up like any built-in.
- `-r`/`--root` picks the repo whose plugins load. Untrusted local plugins stay out; the trust
  switch is in [plugins.md](plugins.md).
- Whatever the loader skipped or failed to import is printed as a warning on stderr, so a rule is
  never missing without a reason and `--json` on stdout stays parseable.
- A `.auditor/config.toml` that fails validation exits 1 with that error instead of printing a
  catalogue the repo's config does not agree with.
- `auditr plugins list` covers the same detectors plus the languages, reporters, and any loader
  warning; see [plugins.md](plugins.md).
