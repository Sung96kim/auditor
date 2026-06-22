"""Pretty-print render functions for CLI commands.

Each function takes a ``Console`` and the command's payload and renders a human-friendly
rich table or panel.  They are independently callable so tests can exercise the pretty
path directly without a TTY.

Accent colour ``#7C7CFF`` matches the rest of the auditor CLI (see self_update.py).
"""

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

_ACCENT = "#7C7CFF"
_BORDER = "dim"


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


def render_graph_build(out: Console, payload: dict[str, Any]) -> None:
    t = Table.grid(padding=(0, 3))
    t.add_column(style="bold")
    t.add_column(justify="right", style=_ACCENT)
    for key in ("nodes", "edges", "clusters", "findings"):
        if key in payload:
            t.add_row(key, str(payload[key]))
    out.print(Panel(t, title="graph built", border_style=_BORDER))


def render_graph_related(out: Console, payload: list[dict[str, Any]]) -> None:
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("symbol")
    t.add_column("kind")
    t.add_column("weight", justify="right")
    t.add_column("rank", justify="right")
    for row in payload:
        t.add_row(
            str(row.get("id", "")),
            str(row.get("kind", "")),
            str(row.get("weight", "")),
            str(row.get("rank", "")),
        )
    out.print(t)


def render_graph_neighbors(out: Console, payload: list[dict[str, Any]]) -> None:
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("dir")
    t.add_column("edge")
    t.add_column("symbol")
    t.add_column("kind")
    t.add_column("hops", justify="right")
    for row in sorted(
        payload,
        key=lambda r: (r.get("direction", ""), r.get("hops", 0)),
    ):
        t.add_row(
            str(row.get("direction", "")),
            str(row.get("edge", "")),
            str(row.get("id", "")),
            str(row.get("kind", "")),
            str(row.get("hops", "")),
        )
    out.print(t)


def render_graph_concept(out: Console, payload: dict[str, Any]) -> None:
    label = payload.get("label", "")
    member_count = payload.get("member_count", 0)
    shown = payload.get("shown", 0)
    out.print(
        f"[bold {_ACCENT}]{label}[/] — [dim]{member_count} members[/]"
        + (f", showing {shown}" if shown and shown < member_count else "")
    )
    members = payload.get("members", [])
    if members:
        t = Table(border_style=_BORDER, show_header=False)
        t.add_column("symbol")
        for m in members:
            t.add_row(str(m))
        out.print(t)


def render_graph_clusters(out: Console, payload: list[dict[str, Any]]) -> None:
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("label")
    t.add_column("size", justify="right")
    for row in sorted(payload, key=lambda r: r.get("member_count", 0), reverse=True):
        t.add_row(str(row.get("label", "")), str(row.get("member_count", "")))
    out.print(t)


def render_graph_search(out: Console, payload: list[dict[str, Any]]) -> None:
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("symbol")
    t.add_column("kind")
    t.add_column("rank", justify="right")
    for row in payload:
        t.add_row(
            str(row.get("id", "")),
            str(row.get("kind", "")),
            str(row.get("rank", "")),
        )
    out.print(t)


def render_graph_usages(out: Console, payload: dict[str, Any]) -> None:
    if not payload:
        out.print("[dim]no such symbol[/]")
        return
    out.print(
        f"[bold {_ACCENT}]{payload.get('resolved', '')}[/] "
        f"[dim]({payload.get('kind', '')})[/]  "
        f"used_by [bold]{payload.get('total_in', 0)}[/] · "
        f"depends_on [bold]{payload.get('total_out', 0)}[/]"
    )
    ambiguous = payload.get("ambiguous", [])
    if ambiguous:
        out.print("[yellow]ambiguous[/] — also matched: " + ", ".join(ambiguous))
    for title, key in (("USED BY", "used_by"), ("DEPENDS ON", "depends_on")):
        groups = payload.get(key, {})
        if not groups:
            continue
        t = Table(
            title=title,
            title_justify="left",
            border_style=_BORDER,
            show_header=True,
            header_style="bold",
        )
        t.add_column("edge")
        t.add_column("count", justify="right")
        t.add_column("e.g.")
        for edge, info in sorted(groups.items(), key=lambda kv: -kv[1]["count"]):
            sample = ", ".join(s.split("::")[-1] for s in info.get("sample", []))
            t.add_row(edge, str(info["count"]), sample)
        out.print(t)


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


def render_rules_list(out: Console, payload: list[dict[str, Any]]) -> None:
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("rule_id")
    t.add_column("category")
    t.add_column("severity")
    t.add_column("framework")
    t.add_column("refs")
    for row in payload:
        t.add_row(
            str(row.get("rule_id", "")),
            str(row.get("category", "")),
            str(row.get("default_severity", "")),
            str(row.get("framework") or ""),
            ", ".join(row.get("standard_refs", [])),
        )
    out.print(t)


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


def render_index_add(out: Console, payload: dict[str, Any]) -> None:
    added: list[str] = payload.get("added", [])
    out.print(f"[{_ACCENT}]added {len(added)} file(s)[/]")
    for f in added:
        out.print(f"  [dim]{f}[/]")


def render_index_list(out: Console, payload: list[dict[str, Any]]) -> None:
    if not payload:
        out.print("[dim](scope is empty)[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("file")
    t.add_column("findings", justify="right")
    for row in payload:
        t.add_row(
            str(row.get("path", row.get("file", ""))),
            str(row.get("finding_count", row.get("findings", ""))),
        )
    out.print(t)


def render_index_repos(out: Console, payload: list[dict[str, Any]]) -> None:
    if not payload:
        out.print("[dim](no repos registered)[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("repo")
    for row in payload:
        t.add_row(str(row.get("repo", "")))
    out.print(t)


def render_index_forget(out: Console, payload: dict[str, Any]) -> None:
    removed = payload.get("removed", False)
    repo = payload.get("repo", "")
    if removed:
        out.print(f"[{_ACCENT}]removed[/] {repo}")
    else:
        out.print(f"[dim]nothing to remove for {repo}[/]")


# ---------------------------------------------------------------------------
# ignore
# ---------------------------------------------------------------------------


def render_ignore_add(out: Console, payload: dict[str, Any]) -> None:
    rule_id = payload.get("rule_id", "")
    scope = payload.get("file") or "repo-wide"
    note = payload.get("note")
    out.print(f"[{_ACCENT}]ignore added[/]  [bold]{rule_id}[/]  ({scope})")
    if note:
        out.print(f"[yellow]note:[/] {note}")


def render_ignore_list(out: Console, payload: list[dict[str, Any]]) -> None:
    if not payload:
        out.print("[dim](no ignores)[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("id", justify="right")
    t.add_column("rule_id")
    t.add_column("file")
    t.add_column("line", justify="right")
    t.add_column("reason")
    for row in payload:
        t.add_row(
            str(row.get("id", "")),
            str(row.get("rule_id", "")),
            str(row.get("file") or ""),
            str(row.get("line") or ""),
            str(row.get("reason") or ""),
        )
    out.print(t)


def render_ignore_rm(out: Console, payload: dict[str, Any]) -> None:
    selector = payload.get("selector", "")
    out.print(f"[{_ACCENT}]removed[/] ignore {selector}")


def render_ignore_clear(out: Console, payload: dict[str, Any]) -> None:
    n = payload.get("cleared", 0)
    out.print(f"[{_ACCENT}]cleared[/] {n} ignore(s)")


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def render_manifest_list(out: Console, payload: list[dict[str, Any]]) -> None:
    if not payload:
        out.print("[dim](no entries)[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("line", justify="right")
    t.add_column("kind")
    t.add_column("symbol")
    for row in payload:
        t.add_row(
            str(row.get("line", "")),
            str(row.get("kind", "")),
            str(row.get("symbol", "")),
        )
    out.print(t)


# ---------------------------------------------------------------------------
# plugins
# ---------------------------------------------------------------------------


def render_plugins_list(out: Console, payload: dict[str, Any]) -> None:
    for section in ("detectors", "languages", "reporters", "profiles"):
        items = payload.get(section)
        if not items:
            continue
        t = Table(
            border_style=_BORDER, show_header=True, header_style="bold", title=section
        )
        t.add_column("name")
        t.add_column("source")
        sources: dict[str, str] = payload.get("_sources", {})
        if isinstance(items, dict):
            for name, _val in items.items():
                t.add_row(name, str(sources.get(name, "")))
        elif isinstance(items, list):
            for name in items:
                t.add_row(str(name), str(sources.get(str(name), "")))
        out.print(t)
    warnings: list[str] = payload.get("warnings", [])
    for w in warnings:
        out.print(f"[yellow]warning:[/] {w}")


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def render_discover(out: Console, payload: list[dict[str, Any]]) -> None:
    if not payload:
        out.print("[dim](no files found)[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("file")
    t.add_column("role")
    for row in payload:
        t.add_row(str(row.get("file", "")), str(row.get("role", "")))
    out.print(t)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def render_config_show(out: Console, payload: dict[str, Any]) -> None:
    out.print_json(data=payload)


# ---------------------------------------------------------------------------
# crossfile
# ---------------------------------------------------------------------------


def render_crossfile(out: Console, payload: dict[str, Any]) -> None:
    n = payload.get("cross_file_findings", 0)
    out.print(f"[{_ACCENT}]cross-file findings:[/] {n}")
