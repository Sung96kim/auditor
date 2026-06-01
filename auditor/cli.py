"""Command-line interface (typer). Commands emit compact JSON for agents; the async engine
is bridged with ``asyncio.run``.
"""

import ast
import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from auditor import crossfile as crossfile_pass
from auditor.aggregate import AuditAggregator
from auditor.config import load_config
from auditor.discovery import FileDiscovery, find_root
from auditor.engine import ScanEngine, audit_target
from auditor.index import IndexStore
from auditor.languages.python.auditor import PythonAuditor
from auditor.models import ManifestEntry, ScanResult
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY
from auditor.reporters import render
from auditor.roles import RoleClassifier

app = typer.Typer(no_args_is_help=True, add_completion=False, help="A token-efficient repo auditor.")
index_app = typer.Typer(no_args_is_help=True, help="Manage the audit-scope index + cache.")
config_app = typer.Typer(no_args_is_help=True, help="Inspect resolved configuration.")
rules_app = typer.Typer(no_args_is_help=True, help="Inspect detector rules.")
plugins_app = typer.Typer(no_args_is_help=True, help="Inspect loaded plugins.")
app.add_typer(index_app, name="index")
app.add_typer(config_app, name="config")
app.add_typer(rules_app, name="rules")
app.add_typer(plugins_app, name="plugins")


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2))


def _index_db(root: Path) -> Path:
    return root / ".auditor" / "index.db"


# --------------------------------------------------------------------- scan


@app.command()
def scan(
    target: Annotated[Path, typer.Argument(help="File or directory to audit.")] = Path("."),
    incremental: Annotated[bool, typer.Option(help="Use/update the on-disk cache.")] = False,
    no_index: Annotated[bool, typer.Option("--no-index", help="Force stateless (no cache).")] = False,
    strict_tests: Annotated[bool, typer.Option(help="Audit tests at production strength.")] = False,
    allow_local_plugins: Annotated[bool, typer.Option(help="Load .auditor/plugins/*.py.")] = False,
    fmt: Annotated[str, typer.Option("--format", help="json | sarif | md")] = "json",
) -> None:
    """Audit a file or directory."""
    results = asyncio.run(
        audit_target(
            target,
            incremental=incremental,
            no_index=no_index,
            strict_tests=strict_tests,
            allow_local_plugins=allow_local_plugins,
        )
    )
    typer.echo(render(results, fmt))


# --------------------------------------------------------------- manifest/report


@app.command()
def manifest(file: Annotated[Path, typer.Argument(help="Python file.")]) -> None:
    """Print the AST class+function manifest for one file (no detectors)."""
    tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
    entries = ManifestEntry.from_module(tree)
    _echo_json([e.model_dump(mode="json") for e in entries])


@app.command()
def report(
    file: Annotated[Path, typer.Argument(help="Python file.")],
    fmt: Annotated[str, typer.Option("--format", help="json | sarif | md")] = "json",
) -> None:
    """Audit one file (stateless) — manifest + findings in one call."""
    result = asyncio.run(_report(file))
    typer.echo(render([result], fmt))


async def _report(file: Path) -> ScanResult:
    root = find_root(file)
    engine = ScanEngine(root, load_config(root))
    return await engine.scan_file(file)


# --------------------------------------------------------------- discover


@app.command()
def discover(
    target: Annotated[Path, typer.Argument()] = Path("."),
) -> None:
    """List auditable files with their classified role."""
    root = find_root(target)
    classifier = RoleClassifier(load_config(root).role_globs)
    out = []
    for path in FileDiscovery(root).files(target):
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        role = classifier.classify(rel, path.read_text(encoding="utf-8", errors="replace"))
        out.append({"file": rel, "role": role.value})
    _echo_json(out)


# --------------------------------------------------------------- aggregate


@app.command()
def aggregate(
    target: Annotated[Path, typer.Argument()] = Path("."),
    out: Annotated[Path, typer.Option("-o", "--out", help="Write AUDIT.md here.")] = Path("AUDIT.md"),
) -> None:
    """Roll up the index into AUDIT.md (run `scan --incremental` first)."""
    root = find_root(target)
    path = asyncio.run(_aggregate(root, out))
    typer.echo(f"wrote {path}")


async def _aggregate(root: Path, out: Path) -> Path:
    async with await IndexStore.connect(_index_db(root)) as index:
        return await AuditAggregator(index).write(out)


# --------------------------------------------------------------- crossfile


@app.command()
def crossfile(target: Annotated[Path, typer.Argument()] = Path(".")) -> None:
    """Recompute cross-file duplicate findings from the index."""
    root = find_root(target)
    count = asyncio.run(_crossfile(root))
    _echo_json({"cross_file_findings": count})


async def _crossfile(root: Path) -> int:
    async with await IndexStore.connect(_index_db(root)) as index:
        per_file = await crossfile_pass.run(index)
        return sum(len(v) for v in per_file.values())


# --------------------------------------------------------------- index subcommands


@index_app.command("add")
def index_add(
    paths: Annotated[list[Path], typer.Argument(help="Files to register in the audit scope.")],
    target: Annotated[Path, typer.Option("--root")] = Path("."),
) -> None:
    """Register files as the audit scope."""
    root = find_root(target)
    rels = [str(p.relative_to(root)) if p.is_relative_to(root) else str(p) for p in paths]
    asyncio.run(_index_add(root, rels))
    _echo_json({"added": rels})


async def _index_add(root: Path, rels: list[str]) -> None:
    async with await IndexStore.connect(_index_db(root)) as index:
        await index.add_scope(rels)


@index_app.command("list")
def index_list(target: Annotated[Path, typer.Option("--root")] = Path(".")) -> None:
    """List the registered scope + per-file counts."""
    root = find_root(target)
    _echo_json(asyncio.run(_index_list(root)))


async def _index_list(root: Path) -> list[dict]:
    async with await IndexStore.connect(_index_db(root)) as index:
        return [e.model_dump(mode="json") for e in await index.files()]


# --------------------------------------------------------------- config/rules/plugins


@config_app.command("show")
def config_show(target: Annotated[Path, typer.Option("--root")] = Path(".")) -> None:
    """Print the resolved configuration."""
    settings = load_config(find_root(target))
    _echo_json(settings.model_dump(mode="json"))


@rules_app.command("list")
def rules_list(
    category: Annotated[str | None, typer.Option(help="Filter by category.")] = None,
    standard: Annotated[str | None, typer.Option(help="bandit | owasp coverage.")] = None,
) -> None:
    """List every registered detector rule."""
    rows = []
    for rid in sorted(REGISTRY.rule_ids()):
        det = REGISTRY.detector(rid)
        if category and str(det.category) != category:
            continue
        refs = list(det.standard_refs)
        if standard and not any(r.startswith(f"{standard}:") for r in refs):
            continue
        rows.append(
            {
                "rule_id": rid,
                "category": str(det.category),
                "default_severity": det.default_severity.value,
                "verdict_kind": det.verdict_kind.value,
                "standard_refs": refs,
                "source": REGISTRY.source_of("detector", rid),
            }
        )
    _echo_json(rows)


@plugins_app.command("list")
def plugins_list(target: Annotated[Path, typer.Option("--root")] = Path(".")) -> None:
    """Show every loaded detector/language/reporter/profile and its source."""
    loader = PluginLoader()
    load_config(find_root(target), loader=loader)
    payload = REGISTRY.snapshot()
    payload["warnings"] = loader.warnings
    _echo_json(payload)


# ensure all built-in languages register for discovery's suffix list
_ = PythonAuditor


if __name__ == "__main__":
    app()
