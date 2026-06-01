"""HTML reporter — a self-contained, styleable page for viewing an audit in a browser.

No external assets (inline CSS), so the single string drops straight onto a temp local
server (``auditor scan --serve``) or into a file. Files and findings are ordered
worst-severity-first, mirroring the JSON/markdown reporters.
"""

import html
from typing import ClassVar

from auditor.models import (
    SEVERITIES_DESC,
    Finding,
    ScanResult,
    Severity,
    severity_rank,
)
from auditor.reporters.base import Reporter

_SEVERITY_HUE: dict[Severity, str] = {
    Severity.BLOCKING: "#b3123b",
    Severity.HIGH: "#c2410c",
    Severity.MEDIUM: "#b08900",
    Severity.LOW: "#3b6fb0",
}


class HtmlReporter(Reporter):
    format: ClassVar[str] = "html"

    def render(self, results: list[ScanResult]) -> str:
        flagged = sorted(
            (r for r in results if r.findings), key=lambda r: r.severity_key
        )
        totals = _totals(results)
        body = [
            _header(totals, scanned=len(results), flagged=len(flagged)),
            _summary_table(flagged),
            *[_file_section(r) for r in flagged],
        ]
        if not flagged:
            body.append('<p class="clean">No findings. ✅</p>')
        return _DOCUMENT.format(style=_STYLE, body="\n".join(body))


def _header(totals: dict[Severity, int], *, scanned: int, flagged: int) -> str:
    chips = "".join(
        f'<span class="chip sev-{s.value}">{totals[s]} {s.value}</span>'
        for s in SEVERITIES_DESC
    )
    return (
        '<header><h1>Audit report</h1>'
        f'<p class="meta">{scanned} files scanned · {flagged} with findings</p>'
        f'<div class="chips">{chips}</div></header>'
    )


def _summary_table(flagged: list[ScanResult]) -> str:
    if not flagged:
        return ""
    head = (
        "<tr><th>File</th><th>Role</th>"
        + "".join(f"<th>{s.value}</th>" for s in SEVERITIES_DESC)
        + "</tr>"
    )
    rows = []
    for r in flagged:
        c = r.counts
        cells = "".join(
            f'<td class="num{" hit" if c[s] else ""}">{c[s] or ""}</td>'
            for s in SEVERITIES_DESC
        )
        anchor = _anchor(r.file)
        rows.append(
            f'<tr><td><a href="#{anchor}"><code>{html.escape(r.file)}</code></a></td>'
            f"<td>{r.role.value}</td>{cells}</tr>"
        )
    return f'<table class="summary"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'


def _file_section(r: ScanResult) -> str:
    findings = sorted(r.findings, key=lambda f: (-severity_rank(f.severity), f.line))
    items = "\n".join(_finding(f) for f in findings)
    anchor = _anchor(r.file)
    return (
        f'<section id="{anchor}"><h2><code>{html.escape(r.file)}</code>'
        f'<span class="role">{r.role.value}</span></h2>{items}</section>'
    )


def _finding(f: Finding) -> str:
    mark = "🔧" if f.verdict_kind.value == "auto" else "🔎"
    refs = "".join(
        f'<span class="ref">{html.escape(ref)}</span>' for ref in f.standard_refs
    )
    evidence = (
        f'<pre class="evidence">{html.escape(f.evidence)}</pre>' if f.evidence else ""
    )
    suggestion = (
        f'<p class="suggestion">{html.escape(f.suggestion)}</p>'
        if f.suggestion
        else ""
    )
    return (
        f'<div class="finding sev-border-{f.severity.value}">'
        f'<div class="finding-head"><span class="sev sev-{f.severity.value}">'
        f"{f.severity.value}</span>"
        f'<span class="mark" title="{f.verdict_kind.value}">{mark}</span>'
        f'<code class="rule">{html.escape(f.rule_id)}</code>'
        f'<span class="loc">L{f.line}</span>{refs}</div>'
        f'<p class="message">{html.escape(f.message)}</p>'
        f"{evidence}{suggestion}</div>"
    )


def _anchor(file: str) -> str:
    return "f-" + "".join(ch if ch.isalnum() else "-" for ch in file)


def _totals(results: list[ScanResult]) -> dict[Severity, int]:
    out = {s: 0 for s in Severity}
    for r in results:
        for sev, n in r.counts.items():
            out[sev] += n
    return out


_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  margin: 0; padding: 2rem; max-width: 960px; margin-inline: auto;
  color: #1c1e21; background: #f6f7f9; }
@media (prefers-color-scheme: dark) {
  body { color: #e4e6eb; background: #16181c; }
  table, .finding { background: #1e2127 !important; }
  code, pre { background: #2a2e36 !important; }
}
h1 { margin: 0 0 .25rem; font-size: 1.6rem; }
.meta { margin: 0 0 1rem; opacity: .7; }
.chips { display: flex; gap: .5rem; flex-wrap: wrap; margin-bottom: 2rem; }
.chip { padding: .25rem .6rem; border-radius: 999px; font-weight: 600; font-size: .85rem;
  color: #fff; }
code { background: #eceef1; padding: .1rem .35rem; border-radius: 4px;
  font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
table.summary { width: 100%; border-collapse: collapse; margin-bottom: 2.5rem;
  background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.summary th, .summary td { padding: .5rem .75rem; text-align: left; border-bottom: 1px solid rgba(128,128,128,.18); }
.summary th:not(:nth-child(-n+2)), .summary td.num { text-align: right; font-variant-numeric: tabular-nums; }
.summary td.num.hit { font-weight: 700; }
.summary a { text-decoration: none; }
section { margin-bottom: 2rem; }
section h2 { font-size: 1.05rem; display: flex; align-items: center; gap: .6rem; }
.role { font-size: .7rem; text-transform: uppercase; letter-spacing: .04em; opacity: .6;
  font-weight: 600; }
.finding { background: #fff; border-radius: 8px; padding: .85rem 1rem; margin: .6rem 0;
  border-left: 4px solid #ccc; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
.finding-head { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
.sev { color: #fff; padding: .1rem .5rem; border-radius: 4px; font-size: .72rem;
  font-weight: 700; text-transform: uppercase; }
.rule { font-weight: 600; }
.loc { opacity: .6; font-size: .82rem; }
.ref { font-size: .72rem; background: rgba(128,128,128,.18); padding: .1rem .4rem;
  border-radius: 4px; }
.message { margin: .5rem 0 .25rem; }
.evidence { background: #f0f1f4; padding: .5rem .75rem; border-radius: 6px; overflow-x: auto;
  font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; margin: .4rem 0; }
.suggestion { margin: .35rem 0 0; opacity: .85; font-style: italic; }
.clean { font-size: 1.2rem; }
""" + "".join(
    f".sev-{s.value}{{background:{hue}}}.sev-border-{s.value}{{border-left-color:{hue}}}"
    for s, hue in _SEVERITY_HUE.items()
)

_DOCUMENT = (
    "<!doctype html><html lang=en><head><meta charset=utf-8>"
    '<meta name=viewport content="width=device-width, initial-scale=1">'
    "<title>Audit report</title><style>{style}</style></head>"
    "<body>{body}</body></html>"
)
