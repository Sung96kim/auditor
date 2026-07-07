"""Orchestration-layer detectors — two mirrored rules split out of oop.py's size budget.

``PY-OOP-FREE-FN-ORCHESTRATOR`` flags a free-function pipeline threading shared state in a
*domain* module (a class in disguise) but exempts CLI command modules, whose free-function
shape the framework prescribes. ``PY-OOP-LOGIC-IN-CLI`` is its complement: it only looks
*inside* CLI command modules, at what the commands do — substantive non-presentation work
belongs in a domain object the command calls.
"""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext
from auditor.languages.python.detectors._util import (
    dotted_name,
    is_cli_command_module,
    kwarg,
)
from auditor.languages.python.detectors.oop import _OopCandidate
from auditor.models import Finding


def _intra_module_callers(
    group: list[ast.FunctionDef | ast.AsyncFunctionDef], names: set[str]
) -> int:
    """How many functions in ``group`` call another top-level function of the module (by name)."""
    return sum(
        1
        for fn in group
        if {dotted_name(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)}
        & names
    )


class FreeFnOrchestrator(_OopCandidate):
    rule_id: ClassVar[str] = "PY-OOP-FREE-FN-ORCHESTRATOR"
    checklist_item: ClassVar[int] = 19

    def run(self, ctx: AuditContext) -> list[Finding]:
        # Typer/Click command modules thread the CLI context between free-function commands by
        # framework design; a coordinator class would fight the framework.
        if is_cli_command_module(ctx.tree, ctx.config.settings.cli_frameworks):
            return []
        names = {
            n.name
            for n in ctx.tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        # The smell is a pipeline threading one value: 3+ functions sharing a param AND
        # calling each other — not independent helpers or CLI handlers.
        for param, group in _functions_by_shared_param(ctx.tree).items():
            if len(group) >= 3 and _intra_module_callers(group, names) >= 2:
                return [
                    self.make_finding(
                        ctx,
                        line=group[0].lineno,
                        message=f"{len(group)} free functions thread `{param}` between them; use a coordinator class",
                        suggestion="encapsulate the pipeline in an X-Coordinator/Index class that holds the state",
                    )
                ]
        return []


#: calls that are substantive non-presentation work when they appear inline in a CLI command:
#: subprocess orchestration and filesystem mutation. Reads/echo/render calls stay free.
_CLI_WORK_CALLS = {
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "os.system",
    "os.popen",
    "os.remove",
    "os.unlink",
    "os.rename",
    "os.replace",
    "os.makedirs",
    "os.mkdir",
    "os.symlink",
    "os.chmod",
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.copytree",
    "shutil.move",
    "shutil.rmtree",
}
_CLI_WORK_SUFFIXES = (
    ".write_text",
    ".write_bytes",
    ".unlink",
    ".mkdir",
    ".touch",
    ".symlink_to",
    ".rmdir",
)


class LogicInCli(_OopCandidate):
    """A function in a CLI command module doing multi-step non-presentation work inline
    (subprocess orchestration, file mutation) instead of delegating to a domain object."""

    rule_id: ClassVar[str] = "PY-OOP-LOGIC-IN-CLI"

    def run(self, ctx: AuditContext) -> list[Finding]:
        if not is_cli_command_module(ctx.tree, ctx.config.settings.cli_frameworks):
            return []
        threshold = ctx.config.effective(self.rule_id).threshold.oop.cli_logic_min_calls
        out: list[Finding] = []
        for fn in ctx.tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            work = [
                name
                for call in ast.walk(fn)
                if isinstance(call, ast.Call) and (name := _cli_work_call(call))
            ]
            if len(work) >= threshold:
                out.append(
                    self.make_finding(
                        ctx,
                        line=fn.lineno,
                        message=f"`{fn.name}` does {len(work)} subprocess/file-mutation calls inline in the CLI layer; delegate to a domain object",
                        evidence=", ".join(sorted(set(work))),
                        suggestion="move the work into a domain class the command calls; keep the CLI layer to parsing and presentation",
                    )
                )
        return out


def _cli_work_call(call: ast.Call) -> str | None:
    name = dotted_name(call.func)
    if name in _CLI_WORK_CALLS or name.endswith(_CLI_WORK_SUFFIXES):
        return name
    if name == "open" and _open_mode_writes(call):
        return name
    return None


def _open_mode_writes(call: ast.Call) -> bool:
    mode = call.args[1] if len(call.args) > 1 else kwarg(call, "mode")
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and any(ch in mode.value for ch in "wax")
    )


def _functions_by_shared_param(
    tree: ast.Module,
) -> dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Group top-level functions by a parameter name they share (the threaded state)."""
    groups: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        a = node.args
        for p in a.posonlyargs + a.args + a.kwonlyargs:
            if p.arg in ("self", "cls"):
                continue
            groups.setdefault(p.arg, []).append(node)
    return groups
