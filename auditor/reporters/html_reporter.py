"""HTML reporter — a self-contained page for viewing an audit in a browser.

Two-pane layout: a sticky, collapsible directory tree on the left (the table of contents)
and the findings on the right. No external assets (inline CSS + a tiny scrollspy script),
so the single string drops straight onto a temp local server (``auditor scan --serve``) or
into a file. Files and findings are ordered worst-severity-first, mirroring the other
reporters.
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


class _TreeNode:
    """A directory (or the implicit root) in the sidebar file tree."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.children: dict[str, _TreeNode] = {}
        self.files: list[ScanResult] = []

    def add(self, result: ScanResult) -> None:
        node = self
        parts = result.file.split("/")
        for part in parts[:-1]:
            node = node.children.setdefault(part, _TreeNode(part))
        node.files.append(result)

    def counts(self) -> dict[Severity, int]:
        out = {s: 0 for s in Severity}
        for child in self.children.values():
            for sev, n in child.counts().items():
                out[sev] += n
        for f in self.files:
            for sev, n in f.counts.items():
                out[sev] += n
        return out

    def render(self) -> str:
        parts = []
        for name in sorted(self.children):
            child = self.children[name]
            parts.append(
                '<details class="dir"><summary>'
                f'<span class="dname">{html.escape(name)}/</span>'
                f"{_badges(child.counts())}</summary>{child.render()}</details>"
            )
        for r in sorted(self.files, key=lambda r: r.file):
            parts.append(_tree_file(r))
        return f'<div class="branch">{"".join(parts)}</div>'


class HtmlReporter(Reporter):
    format: ClassVar[str] = "html"

    def render(self, results: list[ScanResult]) -> str:
        flagged = sorted(
            (r for r in results if r.findings), key=lambda r: r.severity_key
        )
        header = _header(_totals(results), scanned=len(results), flagged=len(flagged))
        if not flagged:
            body = f'{header}<p class="clean">No findings. ✅</p>'
        else:
            tree = _TreeNode("")
            for r in flagged:
                tree.add(r)
            sections = "\n".join(self._section(r) for r in flagged)
            body = (
                f"{header}<div class=layout>"
                "<nav class=toc><div class=\"toc-head\"><span class=\"toc-title\">Files</span>"
                '<span class="toc-actions"><button id="expand-all">Expand all</button>'
                '<button id="collapse-all">Collapse all</button></span></div>'
                f"{tree.render()}</nav>"
                f'<main>{sections}<p class="empty" id="empty" hidden>'
                "No findings match the filter.</p></main></div>"
            )
        return _DOCUMENT.format(style=_STYLE, body=body, script=_SCRIPT)

    def _section(self, r: ScanResult) -> str:
        findings = sorted(
            r.findings, key=lambda f: (-severity_rank(f.severity), f.line)
        )
        items = "\n".join(self._finding(f) for f in findings)
        return (
            f'<section id="{_anchor(r.file)}"><h2><code>{html.escape(r.file)}</code>'
            f'<span class="role">{r.role.value}</span></h2>{items}</section>'
        )

    def _finding(self, f: Finding) -> str:
        mark = "🔧" if f.verdict_kind.value == "auto" else "🔎"
        refs = "".join(
            f'<span class="ref">{html.escape(ref)}</span>' for ref in f.standard_refs
        )
        evidence = (
            f'<pre class="evidence">{html.escape(f.evidence)}</pre>'
            if f.evidence
            else ""
        )
        suggestion = (
            f'<p class="suggestion">{html.escape(f.suggestion)}</p>'
            if f.suggestion
            else ""
        )
        return (
            f'<div class="finding sev-border-{f.severity.value}" data-sev="{f.severity.value}">'
            f'<div class="finding-head"><span class="sev sev-{f.severity.value}">'
            f"{f.severity.value}</span>"
            f'<span class="mark" title="{f.verdict_kind.value}">{mark}</span>'
            f'<code class="rule">{html.escape(f.rule_id)}</code>'
            f'<span class="loc">L{f.line}</span>{refs}</div>'
            f'<p class="message">{html.escape(f.message)}</p>'
            f"{evidence}{suggestion}</div>"
        )


def _header(totals: dict[Severity, int], *, scanned: int, flagged: int) -> str:
    chips = "".join(
        f'<button class="chip sev-{s.value}" data-sev="{s.value}">{totals[s]} {s.value}</button>'
        for s in SEVERITIES_DESC
    )
    search = (
        '<input id="q" type="search" placeholder="Filter findings…" autocomplete="off">'
        if flagged
        else ""
    )
    return (
        "<header><h1>Audit report</h1>"
        f'<p class="meta">{scanned} files scanned · {flagged} with findings</p>'
        f'<div class="controls">{search}<div class="chips">{chips}</div></div></header>'
    )


def _tree_file(r: ScanResult) -> str:
    name = r.file.rsplit("/", 1)[-1]
    worst = _worst_severity(r)
    return (
        f'<a class="tfile" href="#{_anchor(r.file)}">'
        f'<span class="dot sev-{worst.value}"></span>'
        f'<span class="fname">{html.escape(name)}</span>'
        f"{_badges(r.counts)}</a>"
    )


def _badges(counts: dict[Severity, int]) -> str:
    bits = "".join(
        f'<span class="b sev-{s.value}">{counts[s]}</span>'
        for s in SEVERITIES_DESC
        if counts[s]
    )
    return f'<span class="counts">{bits}</span>'


def _worst_severity(r: ScanResult) -> Severity:
    counts = r.counts
    return next((s for s in SEVERITIES_DESC if counts[s]), Severity.LOW)


def _anchor(file: str) -> str:
    return "f-" + "".join(ch if ch.isalnum() else "-" for ch in file)


def _totals(results: list[ScanResult]) -> dict[Severity, int]:
    out = {s: 0 for s in Severity}
    for r in results:
        for sev, n in r.counts.items():
            out[sev] += n
    return out


_STYLE = """
:root { color-scheme: light dark;
  --bg:#eef1f6; --bg2:#e3e8f0; --card:#ffffff; --line:rgba(20,30,60,.1);
  --fg:#1a1d24; --muted:#6b7280; --accent:#5b6cff;
  --shadow:0 1px 2px rgba(20,30,60,.06), 0 6px 20px -12px rgba(20,30,60,.25); }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --bg2:#14171d; --card:#1a1d24; --line:rgba(255,255,255,.09);
    --fg:#e6e8ee; --muted:#8b93a3; --accent:#7c8bff;
    --shadow:0 1px 2px rgba(0,0,0,.3), 0 8px 24px -14px rgba(0,0,0,.6); }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, system-ui, sans-serif;
  margin: 0; padding: 2rem clamp(1rem, 4vw, 3rem); color: var(--fg);
  background: radial-gradient(1200px 600px at 80% -10%, var(--bg2), var(--bg)) fixed;
  -webkit-font-smoothing: antialiased; }
::selection { background: color-mix(in srgb, var(--accent) 30%, transparent); }
h1 { margin: 0; font-size: 1.7rem; font-weight: 800; letter-spacing: -.02em;
  display: flex; align-items: center; gap: .55rem; }
h1::before { content: ""; width: .55rem; height: 1.45rem; border-radius: 3px;
  background: linear-gradient(180deg, var(--accent), color-mix(in srgb, var(--accent) 50%, #b3123b)); }
.meta { margin: .35rem 0 1.1rem; color: var(--muted); }
.controls { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin-bottom: 1.75rem; }
.chips { display: flex; gap: .5rem; flex-wrap: wrap; }
.chip { padding: .3rem .7rem; border-radius: 999px; font-weight: 700; font-size: .8rem;
  color: #fff; border: none; cursor: pointer; transition: opacity .12s, transform .08s;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.15); }
.chip:hover { transform: translateY(-1px); }
.chip:active { transform: translateY(0); }
.chip.off { opacity: .3; text-decoration: line-through; }
#q { padding: .5rem .85rem; border-radius: 10px; border: 1px solid var(--line);
  background: var(--card); color: var(--fg); font: inherit; min-width: 260px;
  box-shadow: var(--shadow); transition: border-color .12s, box-shadow .12s; }
#q:focus { outline: none; border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 22%, transparent); }
.empty { color: var(--muted); padding: 2rem 0; text-align: center; }
code { background: color-mix(in srgb, var(--fg) 7%, transparent); padding: .12rem .4rem;
  border-radius: 5px; font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }

.layout { display: flex; gap: 1.75rem; align-items: flex-start; }
.toc { position: sticky; top: 1.25rem; flex: 0 0 300px; max-height: calc(100vh - 2.5rem);
  overflow: auto; background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: .85rem; font-size: .9rem; box-shadow: var(--shadow);
  scrollbar-width: thin; scrollbar-color: var(--line) transparent; }
.toc::-webkit-scrollbar { width: 9px; }
.toc::-webkit-scrollbar-thumb { background: var(--line); border-radius: 9px; border: 2px solid var(--card); }
.toc-head { display: flex; align-items: center; justify-content: space-between;
  gap: .5rem; padding: .15rem .35rem .6rem; }
.toc-title { font-weight: 700; text-transform: uppercase; letter-spacing: .07em;
  font-size: .68rem; color: var(--muted); }
.toc-actions { display: flex; gap: .3rem; }
.toc-actions button { font-size: .68rem; color: var(--muted); background: transparent;
  border: 1px solid var(--line); border-radius: 6px; padding: .15rem .4rem; cursor: pointer;
  transition: background .1s, color .1s; }
.toc-actions button:hover { background: color-mix(in srgb, var(--fg) 7%, transparent); color: var(--fg); }
.branch { display: flex; flex-direction: column; }
.dir > .branch { margin-left: .6rem; padding-left: .55rem; border-left: 1px solid var(--line); }
details.dir > summary { cursor: pointer; list-style: none; display: flex; align-items: center;
  gap: .35rem; padding: .22rem .35rem; border-radius: 7px; user-select: none; transition: background .1s; }
details.dir > summary::-webkit-details-marker { display: none; }
details.dir > summary::before { content: "▸"; font-size: .7rem; color: var(--muted);
  transition: transform .15s ease; }
details.dir[open] > summary::before { transform: rotate(90deg); }
.dname { font-weight: 600; }
.tfile { display: flex; align-items: center; gap: .45rem; padding: .22rem .35rem;
  border-radius: 7px; text-decoration: none; color: inherit; transition: background .1s; }
.tfile:hover, details.dir > summary:hover { background: color-mix(in srgb, var(--fg) 6%, transparent); }
.tfile.active { background: color-mix(in srgb, var(--accent) 16%, transparent); font-weight: 600;
  box-shadow: inset 2px 0 0 var(--accent); }
.fname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto;
  box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 0%, transparent); }
.counts { margin-left: auto; display: flex; gap: .22rem; }
.counts .b { color: #fff; border-radius: 5px; font-size: .67rem; font-weight: 700;
  padding: .05rem .32rem; min-width: 1.15rem; text-align: center; }

main { flex: 1; min-width: 0; }
section { margin-bottom: 2.25rem; scroll-margin-top: 1.25rem; }
section h2 { font-size: 1.05rem; display: flex; align-items: center; gap: .6rem;
  margin: 0 0 .65rem; padding-bottom: .55rem; border-bottom: 1px solid var(--line); }
.role { font-size: .68rem; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); font-weight: 700; padding: .12rem .45rem; border: 1px solid var(--line);
  border-radius: 999px; }
.finding { background: var(--card); border-radius: 12px; padding: .95rem 1.1rem; margin: .65rem 0;
  border: 1px solid var(--line); border-left: 4px solid #ccc; box-shadow: var(--shadow);
  transition: transform .1s, box-shadow .12s; }
.finding:hover { transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(20,30,60,.08), 0 14px 30px -16px rgba(20,30,60,.4); }
.finding-head { display: flex; align-items: center; gap: .55rem; flex-wrap: wrap; }
.sev { color: #fff; padding: .12rem .55rem; border-radius: 999px; font-size: .68rem;
  font-weight: 800; text-transform: uppercase; letter-spacing: .03em; }
.mark { font-size: .95rem; }
.rule { font-weight: 700; }
.loc { color: var(--muted); font-size: .82rem; }
.ref { font-size: .68rem; color: var(--muted); background: color-mix(in srgb, var(--fg) 6%, transparent);
  padding: .12rem .45rem; border-radius: 999px; }
.message { margin: .6rem 0 .3rem; }
.evidence { background: color-mix(in srgb, var(--fg) 5%, transparent); padding: .6rem .8rem;
  border-radius: 8px; overflow-x: auto; margin: .5rem 0; border: 1px solid var(--line);
  font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
.suggestion { margin: .4rem 0 0; color: var(--muted); }
.suggestion::before { content: "💡 "; }
.clean { font-size: 1.3rem; font-weight: 600; }
@media (max-width: 760px) {
  .layout { flex-direction: column; }
  .toc { position: static; flex: none; width: 100%; max-height: 45vh; }
}
""" + "".join(
    f".sev-{s.value}{{background:{hue}}}.sev-border-{s.value}{{border-left-color:{hue}}}"
    f".dot.sev-{s.value}{{background:{hue};color:{hue}}}"
    for s, hue in _SEVERITY_HUE.items()
)

# Scrollspy (highlight the in-view file in the tree) + live filter (severity toggles + search).
_SCRIPT = """
(()=>{
const links=new Map();
document.querySelectorAll('.tfile').forEach(a=>links.set(a.getAttribute('href').slice(1),a));
const obs=new IntersectionObserver(es=>{es.forEach(e=>{const a=links.get(e.target.id);
if(a&&e.isIntersecting){document.querySelectorAll('.tfile.active').forEach(x=>x.classList.remove('active'));
a.classList.add('active');a.scrollIntoView({block:'nearest'});}});},{rootMargin:'0px 0px -75% 0px'});
document.querySelectorAll('main section').forEach(s=>obs.observe(s));
const sev=new Set(['blocking','high','medium','low']);let q='';
const empty=document.getElementById('empty');
function apply(){let total=0;
document.querySelectorAll('main section').forEach(sec=>{
const fileText=sec.querySelector('h2').textContent.toLowerCase();const fileHit=!q||fileText.includes(q);
let vis=0;sec.querySelectorAll('.finding').forEach(f=>{
const show=sev.has(f.dataset.sev)&&(fileHit||f.textContent.toLowerCase().includes(q));
f.style.display=show?'':'none';if(show)vis++;});
sec.style.display=vis?'':'none';total+=vis;
const a=links.get(sec.id);if(a)a.style.display=vis?'':'none';});
document.querySelectorAll('details.dir').forEach(d=>{
d.style.display=[...d.querySelectorAll('.tfile')].some(a=>a.style.display!=='none')?'':'none';});
if(empty)empty.hidden=total>0;}
document.querySelectorAll('button.chip').forEach(c=>c.addEventListener('click',()=>{
const s=c.dataset.sev;if(sev.has(s)){sev.delete(s);c.classList.add('off');}else{sev.add(s);c.classList.remove('off');}apply();}));
const qi=document.getElementById('q');if(qi)qi.addEventListener('input',()=>{q=qi.value.toLowerCase().trim();apply();});
const setAll=open=>document.querySelectorAll('details.dir').forEach(d=>{d.open=open;});
const ea=document.getElementById('expand-all');if(ea)ea.addEventListener('click',()=>setAll(true));
const ca=document.getElementById('collapse-all');if(ca)ca.addEventListener('click',()=>setAll(false));
})();
"""

_DOCUMENT = (
    "<!doctype html><html lang=en><head><meta charset=utf-8>"
    '<meta name=viewport content="width=device-width, initial-scale=1">'
    "<title>Audit report</title><style>{style}</style></head>"
    "<body>{body}<script>{script}</script></body></html>"
)
