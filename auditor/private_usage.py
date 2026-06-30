"""Repo-level: a private (``_``-prefixed) module-level symbol that's referenced from another file.

The leading underscore says "module-internal", but it's imported/used cross-file — so it should
be public (drop the underscore) or stop leaking out. Pure logic over the same symbol def/ref shape
rows the dead-code pass already extracts; no db.

Conservative, to keep a CANDIDATE finding low-noise: only a symbol defined in exactly ONE file is
considered (a name defined in several files can't be attributed to a single definition), only
``production``/``script`` definitions are flagged, and only references from OTHER production/script
files count (a test poking at internals isn't the signal).
"""

from auditor.models import Category, Finding, Severity, VerdictKind

_SEP = "\x1f"
RULE_ID = "PY-XFILE-PRIVATE-USED"
DEF_KIND = "py-symbol-def"
REF_KIND = "py-symbol-ref"

_NOUN = {"func": "function", "class": "class", "const": "constant"}
_EMIT_ROLES = ("production", "script")
_SHOWN = 3  # cap the other-file list shown in the message


def _is_private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def find_leaked_private(
    def_rows: list[dict], ref_rows: list[dict], roles: dict[str, str]
) -> dict[str, list[Finding]]:
    """Private module-level symbols referenced from a different production file → 'make it public'.
    ``def_rows`` are ``py-symbol-def`` rows (``symbol`` = ``"<kind>\\x1f<name>"``); ``ref_rows`` are
    ``py-symbol-ref`` rows (``symbol`` = the referenced name). Both carry ``path``."""
    defs: dict[str, list[dict]] = {}
    for d in def_rows:
        kind, _, name = d["symbol"].partition(_SEP)
        if _is_private(name):
            defs.setdefault(name, []).append(
                {"path": d["path"], "line": d["line"], "kind": kind}
            )

    ref_paths: dict[str, set[str]] = {}
    for r in ref_rows:
        ref_paths.setdefault(r["symbol"], set()).add(r["path"])

    out: dict[str, list[Finding]] = {}
    for name, rows in defs.items():
        def_files = {r["path"] for r in rows}
        if len(def_files) != 1:
            continue  # same private name in 2+ files — can't attribute a cross-file ref to one
        def_path = next(iter(def_files))
        if def_path.rsplit("/", 1)[-1] == "__init__.py":
            continue
        if roles.get(def_path) not in _EMIT_ROLES:
            continue
        elsewhere = sorted(
            p
            for p in ref_paths.get(name, ())
            if p != def_path and roles.get(p) in _EMIT_ROLES
        )
        if not elsewhere:
            continue
        for row in rows:
            out.setdefault(def_path, []).append(
                _finding(row["kind"], name, row["line"], elsewhere)
            )
    return out


def _finding(kind: str, name: str, line: int, elsewhere: list[str]) -> Finding:
    noun = _NOUN.get(kind, "symbol")
    shown = ", ".join(elsewhere[:_SHOWN])
    if len(elsewhere) > _SHOWN:
        shown += f" (+{len(elsewhere) - _SHOWN} more)"
    return Finding(
        rule_id=RULE_ID,
        category=Category.OOP_COMPOSITION,
        severity=Severity.LOW,
        verdict_kind=VerdictKind.CANDIDATE,
        line=line,
        message=(
            f"private {noun} `{name}` is used in another file ({shown}) — the leading `_` "
            "marks it module-internal"
        ),
        evidence=name,
        suggestion="drop the leading underscore to make it public, or stop importing it from other modules",
        detector="private-usage",
    )
