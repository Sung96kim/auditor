"""Supply-chain Python detectors.

``setup.py`` runs arbitrary code during ``pip install`` (sdist builds execute it), so process /
network / eval calls at its *module scope* — or inside a custom ``cmdclass`` build/install
command — are an install-time execution hook: the Python analog of an npm ``postinstall`` and a
classic sdist supply-chain vector. Surfaced as a candidate: a legitimate ``setup.py`` occasionally
shells out (compiling an extension), so the agent judges."""

import ast
from typing import ClassVar

from auditor import ast_util
from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import call_attr
from auditor.models import Category, Finding, Severity, VerdictKind

# callees that run a command / fetch the network / eval code — specific enough at an install
# script's module scope to flag (generic names like `run`/`get`/`call` are excluded to cut FPs)
_SETUP_EXEC_CALLEES = {
    "system",
    "popen",
    "check_call",
    "check_output",
    "Popen",
    "eval",
    "exec",
    "compile",
    "urlopen",
    "urlretrieve",
}

# statements that don't execute at import time (or are imports) — skip; everything else in the
# module body runs when pip builds the sdist
_INERT_AT_IMPORT = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Import,
    ast.ImportFrom,
)


# distutils/setuptools command base classes — a subclass registered via ``cmdclass`` runs during
# the matching build/install step, so exec in its body is the same install-time execution as
# module scope, hidden inside a class the module-scope pass deliberately skips.
_SETUP_COMMAND_BASES = {
    "install",
    "develop",
    "egg_info",
    "build",
    "build_ext",
    "build_py",
    "build_clib",
    "sdist",
    "bdist_egg",
    "bdist_wheel",
    "Command",
    "test",
    "easy_install",
}


def _is_setup_py(file_path: str) -> bool:
    return file_path.rsplit("/", 1)[-1] == "setup.py"


def _is_command_class(node: ast.ClassDef) -> bool:
    # base like `install` / `setuptools.command.install.install` -> last segment "install"
    return any(ast_util.base_name(b) in _SETUP_COMMAND_BASES for b in node.bases)


class SetupPyExec(Detector):
    rule_id: ClassVar[str] = "PY-SUPPLY-SETUP-EXEC"
    category: ClassVar[Category] = Category.SUPPLY_CHAIN
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE

    def run(self, ctx: AuditContext) -> list[Finding]:
        if not _is_setup_py(ctx.file_path):
            return []
        out: list[Finding] = []
        # module-scope execution — runs when pip imports/execs setup.py to build the sdist
        for stmt in ctx.tree.body:
            if not isinstance(stmt, _INERT_AT_IMPORT):
                out += self._exec_calls(ctx, stmt, "at module scope")
        # a custom setuptools/distutils command (a ``cmdclass`` hook) whose body shells out — runs
        # during the matching build/install step, where the module-scope pass can't see it
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.ClassDef) and _is_command_class(node):
                out += self._exec_calls(ctx, node, f"in setup command `{node.name}`")
        return out

    def _exec_calls(
        self, ctx: AuditContext, node: ast.AST, where: str
    ) -> list[Finding]:
        return [
            self.make_finding(
                ctx,
                line=call.lineno,
                message=f"`setup.py` runs `{call_attr(call)}(...)` {where} — code executes on `pip install` (install-time hook)",
                suggestion="confirm this install-time execution is necessary and trusted; move build logic into a reviewed build backend, not setup.py",
            )
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and call_attr(call) in _SETUP_EXEC_CALLEES
        ]
