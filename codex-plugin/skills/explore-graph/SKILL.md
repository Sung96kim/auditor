---
name: explore-graph
description: Explore the semantic code graph: dead code, call/dependency impact, symbol usages (find-refs/go-to-def), clusters. Use when tracing how code connects or whether a finding matters.
paths: "**/*.py, **/*.ts, **/*.tsx"
---

Answer structural questions with auditor's semantic graph. Nothing to install and nothing to
check first: the graph libraries are core dependencies of `auditr`, so the CLI subcommands and the
`graph_*` MCP tools are always available.

## Steps

1. Build once: MCP `graph_build()` or `auditr graph build`. Both auto-scan first by default, so a
   plain rebuild after code changes is already fresh; `--rebuild`/`--no-scan` are for the
   discard-cached-facts and skip-scan edge cases, not routine use.
2. Query (MCP `graph_*` tools, else the CLI):
   - `graph usages <symbol>`: who uses it (`used_by`, full counts) and what it depends on
     (`depends_on`); this is your find-references / go-to-definition, and the right tool for "how
     is X used" (prefer it over `neighbors`, which truncates silently with no totals).
   - `graph related` / `graph neighbors`: nearby code. `related` walks semantic (name/usage
     similarity) edges, `neighbors` walks structural (calls/overrides/...) edges by hop depth.
   - `graph flow <symbol>` reads a whole code path in one call: a tree of what the symbol
     reaches (or `--in`, what reaches it) plus the ordered `modules` list. Reach for it when the
     question is "what does this do end to end"; `usages` stays the find-references query.
   - `graph clusters`: cohesive concept groups; combine with the `GRAPH-GOD-CONCEPT` /
     `GRAPH-SCATTERED-CONCEPT` findings for *why* something is a hotspot, not just its size.
   - `graph search` / `graph concept`: locate by name/term. `search` takes a name *or* a
     plain-English description: ids containing the term come first and, when any do, they are the
     whole page; a term no id contains falls through to the symbols ranked nearest to it by the
     build's tf-idf + LSI fit, each row carrying its cosine as `score`. A name match suppresses
     ranking, a word the build never fitted returns nothing, and a word the corpus holds but no id
     carries *is* ranked, so an empty page means "not in the corpus", not "no such thing". Read a
     ranked page as a shortlist to skim: a third of the retrieval gate's questions have their
     labelled answer on it. Text-sparse symbols and module nodes are reachable by name only.
     `concept` finds the cluster a term belongs to. To go from a symbol you already have to what
     is near it, `graph related` walks name and usage similarity.
   - Every edge carries `provenance`, which is `deterministic` or `refined`. The query payloads
     do not expose it; `graph export --json` and `graph serve` are where it is visible. `refined`
     is two different numbers: on a cluster member it is a per-node 0 or 1 flag, and on a build
     report it is the count of refinements the graph currently applies. When an answer rests on a
     refined edge, say so: it is evidence of a different kind from a deterministic one, and
     `graph refinements list --json` carries the reason each was made for.
   - `graph unresolved` lists what the deterministic resolver could not place, worst first. Use it
     to tell "the graph has no edge here" from "there is genuinely no caller" before trusting an
     empty `used_by`. `--reason ambiguous_name` is the short, high-signal end; rows flagged
     `ext-bound` name a non-repo import, sort last and are noise (`--no-external` hides them).
   - `graph refine <scope>` runs a model over the queue under a path and commits what it proposes;
     `graph refine <scope> --brief` shows what that run would be asked, without opening one.
   - `graph eval` measures what a runner gets right on this repo and stores the numbers that decide
     whether its edge corrections may go active without a human. It costs money per run: on the
     human path the plan and its ceilings print before the first run opens, and `--dry-run --json`
     reads the plan without spending.
   - `graph refinements list` and `graph log` are the recorded corrections and the runs that made
     them, newest first. Read them before proposing a correction of your own: a `pending` row is
     one a human still has to accept, so the graph does not carry it yet.
   - Unfamiliar with any of these, or need real command + output examples? Read
     `references/recipes.md`, which walks "is X dead", "blast radius of changing Y", "hotspots",
     and "locate by name/term" as concrete recipes, plus how to read `used_by`/`depends_on`, edge
     kinds, and when a rebuild is actually needed.
3. For a visual, `auditr graph serve` opens the browser UI (CLI only); `auditr graph export`
   renders a Graphviz DOT/SVG of the graph, a cluster (`--cluster`), a symbol's ego-graph
   (`--symbol --depth`), or a flow tree (`--flow <symbol>`, `--in`, `--depth`).
4. Use `usages` when judging whether a finding matters. Dead code (`used_by` empty, confirmed
   with a string-literal grep for dynamic dispatch before deleting anything) vs. widely-used
   (high `total_in`, wide blast radius).

## References

- `references/recipes.md`: concrete query recipes with real commands and real output from this
  repo: dead-code check, blast-radius check, the entry-point flow read, hotspot/god-concept
  hunting, name/term lookup, how to read `used_by` vs `depends_on` and edge kinds,
  staleness/rebuild rules, and `serve`/`export` for visuals.
