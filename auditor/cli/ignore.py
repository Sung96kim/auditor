"""``auditor ignore add|list|rm|clear`` — manage persistent, db-backed finding suppression.

An ignore is keyed by ``rule_id`` and scoped by the optional ``--file``/``--line``: none = repo
wide, ``--file`` = that file, ``--file`` + ``--line`` = that one finding. Line-level adds snapshot
the offending text so the ignore follows the code when lines shift. Stored in the shared index
(``~/.auditor``), so rescans honor them automatically (see ``scan``/``report`` ``--show-ignored``)."""

import time
from pathlib import Path

import typer

from auditor.cli.helpers import _echo_json, _fail, _open_index, _run
from auditor.cli.options import (
    AllowLocalPlugins,
    IgnoreFile,
    IgnoreForce,
    IgnoreLine,
    IgnoreReason,
    IgnoreRuleId,
    IgnoreSelector,
    RootArg,
)
from auditor.config import load_config
from auditor.discovery import find_root
from auditor.engine import finding_evidence_at
from auditor.ignores import evidence_hash
from auditor.registry import REGISTRY

ignore_app = typer.Typer(
    no_args_is_help=True, help="Manage persistent ignores (suppressed findings)."
)


@ignore_app.command("add")
def ignore_add(
    rule_id: IgnoreRuleId,
    file: IgnoreFile = None,
    line: IgnoreLine = None,
    reason: IgnoreReason = None,
    target: RootArg = Path("."),
    allow_local_plugins: AllowLocalPlugins = False,
    force: IgnoreForce = False,
) -> None:
    """Ignore a rule repo-wide (no scope), in a file (--file), or at one line (--file --line)."""
    if line is not None and file is None:
        _fail("--line requires --file")
    root = find_root(target)
    # load the repo's config so its plugin-contributed rules register and validate like built-ins
    # (entry-point + config plugins always; local .auditor/plugins only when trusted / -a).
    load_config(root, allow_local_plugins=allow_local_plugins)
    if not force and rule_id not in REGISTRY.rule_ids():
        _fail(
            f"unknown rule_id {rule_id!r}; run `auditor rules list` to see rules "
            "(use --allow-local-plugins for an untrusted local plugin rule, or --force to skip)"
        )
    _echo_json(_run(_ignore_add(root, rule_id, file, line, reason), "adding ignore…"))


async def _ignore_add(
    root: Path, rule_id: str, file: str | None, line: int | None, reason: str | None
) -> dict:
    ev_hash: str | None = None
    note: str | None = None
    if line is not None and file is not None:
        evidence = await finding_evidence_at(root, file, rule_id, line)
        if evidence is None:
            note = "no current finding at that line — stored with literal-line fallback"
        else:
            ev_hash = evidence_hash(evidence)
    async with await _open_index(root) as index:
        ignore_id = await index.add_ignore(
            rule_id, file, line, ev_hash, reason, time.time()
        )
    return {
        "id": ignore_id,
        "rule_id": rule_id,
        "file": file,
        "line": line,
        "reason": reason,
        "note": note,
    }


@ignore_app.command("list")
def ignore_list(target: RootArg = Path(".")) -> None:
    """List the ignores recorded for this repo (with their ids)."""
    root = find_root(target)
    _echo_json(_run(_ignore_list(root), "reading ignores…"))


async def _ignore_list(root: Path) -> list[dict]:
    async with await _open_index(root) as index:
        return await index.ignores()


@ignore_app.command("rm")
def ignore_rm(
    selector: IgnoreSelector,
    file: IgnoreFile = None,
    line: IgnoreLine = None,
    target: RootArg = Path("."),
) -> None:
    """Remove an ignore by id (`ignore rm 7`) or by selector (`ignore rm <rule_id> --file …`)."""
    root = find_root(target)
    removed = _run(_ignore_rm(root, selector, file, line), "removing ignore…")
    if not removed:
        _fail(f"no matching ignore for {selector!r}")
    _echo_json({"removed": True, "selector": selector})


async def _ignore_rm(
    root: Path, selector: str, file: str | None, line: int | None
) -> bool:
    async with await _open_index(root) as index:
        if selector.isdigit():
            return await index.remove_ignore_by_id(int(selector))
        return await index.remove_ignore_by_selector(selector, file, line)


@ignore_app.command("clear")
def ignore_clear(target: RootArg = Path(".")) -> None:
    """Remove every ignore for this repo."""
    root = find_root(target)
    _echo_json({"cleared": _run(_ignore_clear(root), "clearing ignores…")})


async def _ignore_clear(root: Path) -> int:
    async with await _open_index(root) as index:
        return await index.clear_ignores()
