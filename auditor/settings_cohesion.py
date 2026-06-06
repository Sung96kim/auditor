"""Repo-level analysis behind ``PY-CONFIG-SCATTERED-SETTINGS``: flag ``BaseSettings`` subclasses
(transitively — any descendant of pydantic ``BaseSettings``) that live outside the project's
settings home. Pure logic over class-base edges collected from the index ``shapes`` table; no db.

Inputs are ``py-class-base`` shape rows whose ``symbol`` is ``"<Class>\\x1f<Base>"`` (one row per
base; a base-less class emits ``"<Class>\\x1f"``). The home is decided two ways, combined:
modules named in ``settings_modules`` (by file stem or a parent dir), and — when ``cohesion`` is on
— the de-facto module where settings classes already cluster.
"""

from collections import Counter
from pathlib import PurePosixPath

from auditor.models import Category, Finding, Severity, VerdictKind

RULE_ID = "PY-CONFIG-SCATTERED-SETTINGS"
_BASESETTINGS = "BaseSettings"
_SEP = "\x1f"


def _module_name_blessed(path: str, names: set[str]) -> bool:
    """True if the file's stem or any parent directory is a declared settings module."""
    p = PurePosixPath(path)
    return p.stem in names or bool(set(p.parts[:-1]) & names)


def _settings_class_names(edges: list[dict]) -> set[str]:
    """Every class name that transitively derives from ``BaseSettings`` (closure over base edges)."""
    children: dict[str, set[str]] = {}
    for edge in edges:
        cls, _, base = edge["symbol"].partition(_SEP)
        if base:
            children.setdefault(base, set()).add(cls)
    settings: set[str] = set()
    stack = [_BASESETTINGS]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in settings:
                settings.add(child)
                stack.append(child)
    return settings


def _occurrences(edges: list[dict], settings: set[str]) -> list[tuple[str, str, int]]:
    """Distinct (class, path, line) sites of a settings class (one class emits several base rows)."""
    seen = {
        (edge["symbol"].partition(_SEP)[0], edge["path"], edge["line"])
        for edge in edges
    }
    return sorted(occ for occ in seen if occ[0] in settings)


def _cohesion_home(items: list[tuple[str, str, int]]) -> str | None:
    """The module holding the most settings classes — or None when there's no unique winner."""
    counts = Counter(path for _, path, _ in items).most_common()
    if not counts or (len(counts) > 1 and counts[0][1] == counts[1][1]):
        return None
    return counts[0][0]


def find_scattered(
    edges: list[dict],
    roles: dict[str, str],
    *,
    settings_modules: list[str],
    cohesion: bool,
) -> dict[str, list[Finding]]:
    """Per-file findings for settings classes living outside the blessed location(s). Evaluated
    within role (a prod settings home and a test fixture aren't compared)."""
    names = set(settings_modules)
    settings = _settings_class_names(edges)
    by_role: dict[str, list[tuple[str, str, int]]] = {}
    for occ in _occurrences(edges, settings):
        by_role.setdefault(roles.get(occ[1], "production"), []).append(occ)

    out: dict[str, list[Finding]] = {}
    for items in by_role.values():
        named = {p for _, p, _ in items if _module_name_blessed(p, names)}
        if named:
            blessed = (
                named  # the named module(s) are home; everything else is scattered
            )
        elif not cohesion:
            blessed = set()  # strict name-only mode, nothing named → all are scattered
        else:
            home = _cohesion_home(items)
            if home is None:
                continue  # ambiguous (tie / none) — don't guess, flag nothing
            blessed = {home}
        for cls, path, line in items:
            if path not in blessed:
                out.setdefault(path, []).append(_finding(cls, line, blessed))
    return out


def _finding(cls: str, line: int, blessed: set[str]) -> Finding:
    where = ", ".join(sorted(blessed)) if blessed else "a config/settings module"
    return Finding(
        rule_id=RULE_ID,
        category=Category.CONFIG,
        severity=Severity.LOW,
        verdict_kind=VerdictKind.CANDIDATE,
        line=line,
        message=f"settings class `{cls}` is defined outside the settings home ({where})",
        evidence=cls,
        suggestion="keep BaseSettings subclasses together in the project's config/settings module",
        detector="settings-cohesion",
        checklist_item=31,
    )
