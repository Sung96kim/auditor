# Worked examples

Real findings from `auditr scan` runs against this repo (auditor auditing itself). Line numbers
are as of the commit these were captured against — re-verify with a fresh scan before reusing.

## 1. False positive → skip-directive

**Finding** (`auditor/status.py:33`, `PY-CORRECT-SWALLOWED-EXCEPTION`, medium):

```json
{
  "rule_id": "PY-CORRECT-SWALLOWED-EXCEPTION",
  "severity": "medium",
  "line": 33,
  "message": "exception silently swallowed (no log, re-raise, or handling)",
  "evidence": "except OSError:"
}
```

The site:

```python
# auditor/status.py
"""Writes the compact status cache the Claude Code plugin's status line reads. ...
Repo-wide only ... written on directory scans ..."""

def write_status(root: Path, results: list[ScanResult], *, configured: bool) -> Path:
    ...
    out = root / ".auditor" / ".status.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({...}))
    except OSError:
        pass  # best-effort cache (gitignored) — a read-only fs must not fail the scan
    return out
```

**Reasoning**: the module docstring states this cache is optional — the status line reads it and
nothing else, but the scan itself must never fail because the cache write failed (read-only fs,
disk full, permissions). The `except OSError: pass` is deliberate and already explained by the
inline comment; there's no caller who needs to observe this failure — `write_status` still
returns the path either way. This is a genuine false positive, not a bug, and the shape
(`try: write cache / except OSError: pass`) is stable — it won't turn into a real bug on a
future edit without someone deliberately removing the comment too.

**Verdict**: skip-directive, placed on the finding's reported line — the `except OSError:` line
(33), not the `pass` line below it:

```python
    except OSError:  # auditor: skip: PY-CORRECT-SWALLOWED-EXCEPTION
        pass  # best-effort cache (gitignored) — a read-only fs must not fail the scan
```

## 2. True positive → fix

**Finding** (`auditor/languages/python/detectors/oop.py:524`, `PY-OOP-HIGH-COMPLEXITY`, low):

```json
{
  "rule_id": "PY-OOP-HIGH-COMPLEXITY",
  "severity": "low",
  "line": 524,
  "message": "`_field_copies` cyclomatic complexity 18 (> 10)",
  "evidence": "def _field_copies(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, int]:",
  "suggestion": "extract helpers; reduce branching"
}
```

The site (abridged): `_field_copies` walks every node in a function body and, in one flat loop
with a nested closure (`bump`), handles three unrelated syntactic shapes in sequence —
`target.attr = source.attr` assigns, tuple-unpack rows (`self.a, self.b = src.a, src.b`), and
constructor kwargs (`Result(a=src.a, b=src.b)`) — each with its own `isinstance` branching.

**Reasoning**: this isn't irreducible domain complexity. The three shapes are independent (an
`ast.Assign` branch, a tuple-unpack branch, an `ast.Call` branch) and never interact — nothing in
one branch depends on state from another beyond the shared `counts` dict. That's the textbook
case the rule's `suggestion` is pointing at: extract helpers. It also matches the file's own
category (`oop-composition`) — a free function accreting responsibilities is exactly what the
category flags.

**Verdict**: fix. Split into three small predicate/bump helpers (`_bump_from_assign`,
`_bump_from_tuple_unpack`, `_bump_from_call_kwargs`), each walking its own node-type check, called
from a slimmer top-level loop. Each helper's complexity drops well under the threshold and the
function reads as "three independent passes," not one tangle.

## 3. The line-anchoring footgun, live in this repo

**Finding** (`auditor/serve.py:67`, `PY-CORRECT-SWALLOWED-EXCEPTION`, medium) — and it's
*still firing* despite a skip directive already sitting three lines away:

```python
# auditor/serve.py
class _ReportHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = self.server.payload
        try:
            self.send_response(200)
            ...
            self.wfile.write(body)
        except (                                                    # line 67 — Finding.line
            BrokenPipeError,
            ConnectionResetError,
        ):  # auditor: skip: PY-CORRECT-SWALLOWED-EXCEPTION          # line 70 — the directive
            # a client disconnect mid-response (reload / navigate away / cancelled a large
            # payload) is nothing to handle — swallowing it is the correct behavior.
            pass
```

**Reasoning**: the finding is a genuine false positive — a client disconnecting mid-response is
exactly the kind of thing you swallow, and the comment already says so. Someone clearly tried to
suppress it. But `Finding.line` for a multi-line `except (...)：` is anchored to the line
carrying the `except` keyword (67, the AST node's `lineno`) — not the line with the closing
`):`. The skip directive was written on line 70. `auditor/skips.py` matches line directives
against the *exact* line number the finding is reported on; a directive on a different line is
inert. Confirmed against the repo: `scan --rule PY-CORRECT-SWALLOWED-EXCEPTION` on this file
still returns the finding, and `suppressed: 0` — the existing directive is doing nothing.

**Verdict**: skip-directive is still the right call — the finding really is a false positive —
but it has to move to line 67, the `except (` line itself:

```python
        except (  # auditor: skip: PY-CORRECT-SWALLOWED-EXCEPTION
            BrokenPipeError,
            ConnectionResetError,
        ):
            # a client disconnect mid-response ...
            pass
```

This is the exact footgun the SKILL body warns about: for any multi-line construct (a
multi-line `except (...)`, a decorated `def`), the reported line is the *statement's* anchor
line (the `except`/`def` keyword), not wherever in the block feels natural to comment. Always
check `finding["line"]` — don't eyeball where to put the directive.
