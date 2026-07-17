## v0.10.0 (2026-07-17)

### Feat

- **graph**: recover edges lost to re-exports, body class-refs, and typed receivers (#5)

## v0.9.3 (2026-07-14)

### Fix

- coerce raw rule-dicts on config serialization (no pydantic warning)

## v0.9.2 (2026-07-14)

### Fix

- silence FastMCP startup banner on the MCP server (stdio log noise)

## v0.9.1 (2026-07-14)

### Fix

- pin uvx --python 3.13 for plugin MCP server (fixes -32000 on py<3.13); plugin 0.1.2

## v0.9.0 (2026-07-14)

### Feat

- Claude Code plugin (skills, subagent, hooks, statusline) + MCP/gate additions (#4)
- content secret sweep over any file (not just code/config)
- config/data-file secret sweep + committed-dotenv rule

### Fix

- exclude node_modules/.claude/session dirs from the sdist (40MB → ~1.4MB)
- stop double-registering auto-loaded hooks.json in plugin manifest (bump plugin 0.1.1)

## v0.8.0 (2026-07-10)

### Feat

- cap MCP tool output volume + reporter/DB/a11y cleanups (v0.8.0) (#3)

## v0.7.0 (2026-07-10)

### Feat

- osv scanner + opt in clamav scan (#2)
- **detector**: add PY/SH-STYLE-LONG-COMMENT (long comment block rules) (#1)

## v0.6.0 (2026-07-06)

### Feat

- **detector**: close audit coverage gaps — widen untyped-dict/lazy-init/field-copy/parallel-sibling, add PY-OOP-TWIN-METHODS + PY-OOP-LOGIC-IN-CLI

## v0.5.0 (2026-06-30)

### Feat

- **cli**: version/self-update banners + animated scan progress; shared console module, public helpers API, rich dep
- **detector**: SA-RAW-SQL variable tracking + PY-XFILE-PRIVATE-USED cross-file check; dogfood cleanups

## v0.4.0 (2026-06-29)

### Feat

- **engine**: single-file audit runs cross-file off the shared index
- **cli**: add pretty version command

## v0.3.0 (2026-06-24)

### Feat

- **graph-ui**: TEXT view mode + 2D/3D/TEXT toggle
- **graph-ui**: 3D graph view component (POC, three.js)
- **graph-ui**: edge kind labels in 2D
- **graph-ui**: draggable nodes; roomier dagre spacing (less crammed)
- **graph-ui**: layered DAG layout (dagre) for cluster/overview so flow reads start→end; ego stays radial
- **graph**: graph build --rebuild discards cached facts + re-extracts (picks up extractor upgrades)
- **graph-ui**: cluster-aware layout seeding (clusters anchored on a ring, members grouped) for organized canvas
- **graph-ui**: animate panel collapse/expand (sidebar width+fade, controls accordion)
- **graph**: opt-in [graph] follow_reexports to resolve calls through package __init__ re-exports
- **graph-ui**: render only structural edges (drop semantic hairball); select isolates the path, click-bg/Esc deselects
- **graph-ui**: LinLog cluster separation, rose method color, edge click selects endpoint, rounded hover box
- **graph-ui**: hovering an edge highlights the edge + its endpoints (the path)
- **graph-ui**: distinct method color, louder hovers, closer zoom-to-label threshold, more cluster spacing
- **graph-ui**: reveal non-path labels on zoom-in when a path is selected
- **graph-ui**: motion throughout the chrome (panels/dropdown/detail/hover); hide non-path labels on select
- **graph-ui**: sidebar node click reveals ego; sidebar type-filter dropdown
- **graph**: graph_usages (grouped connectivity + full counts + disambiguation) and graph_search
- **cli**: pretty terminal output for all commands (raw JSON when piped or --json)
- **mcp**: graph tools auto-scan + compact responses + graph_overview
- **cli**: staged + prettier spinner (graph build/serve show progress)
- **graph**: graph build/serve auto-scan (force extraction) with --no-scan
- **graph**: cleaner UI chrome — top bar, breadcrumb nav, refined panels
- **graph**: prettier canvas (node borders/glow, curved edges) + subtle node animations
- **cli**: clear install hint when graph extra is missing
- **cli**: auditor self update (PyPI check + upgrade, rich logs)
- **graph**: select-in-place node click (highlight+dim, no relayout); double-click to focus
- **graph**: graph serve (inject data + serve interactive UI)
- **graph**: UI panels (explorer/filters/detail/minimap) + findings overlay
- **graph**: sigma graph canvas (overview/cluster/ego)
- **graph**: scaffold Vite+React+sigma UI module (pnpm)
- **graph**: graph export --format dot|svg
- **graph**: viz payload builder (UI data contract)
- **graph**: god-concept uses log-space outlier floors (robust to heavy-tailed centrality)
- **graph**: god-concept splits hub vs high-centrality messaging
- **graph**: run graph detectors during build, persist advisory findings
- **graph**: naming-inconsistency detector (semantic-profile verb distance)
- **graph**: scattered-concept detector
- **graph**: GraphContext + GraphDetector base + god-concept detector
- **graph**: register GRAPH-* detector rule stubs
- **graph**: config knobs for graph detectors
- **graph**: single-source semantic-profile module + per-symbol extraction
- **graph**: personalized PageRank teleporting to non-test nodes
- **graph**: distinctive cross-cluster tf-idf labels
- **graph**: registered_in edges via decorator root + import binding
- **graph**: within-repo module imports edges
- **graph**: exclude module+test nodes from similarity and clustering
- **graph**: module nodes + module->symbol contains edges
- **graph**: MCP graph tools (build/related/neighbors/concept/clusters)
- **graph**: query API (related/neighbors/concept/clusters) + wire CLI commands
- **graph**: repo-pass GraphBuilder + abstractness + graph CLI
- **graph**: concept clustering (kNN sparsify + label propagation)
- **graph**: in-house PageRank over structural edges
- **graph**: usage-similarity edges (callee + type Jaccard, blocked)
- **graph**: naming-similarity edges (tf-idf + LSI, sparse-flagged)
- **graph**: structural edge resolution (calls/inherits/overrides/callback/types)
- **graph**: write per-file facts during scan (opt-in via [tool.auditor.graph])
- **graph**: index persistence (facts cache + nodes/edges/clusters)
- **graph**: per-file AST fact extraction
- **graph**: identifier tokenization + naming-document cascade
- **graph**: data model + [graph] extra

### Fix

- **build**: ship bundled graph UI in sdist (hatch artifacts)
- **graph-ui**: give edges real thickness so directional arrowheads are visible
- **graph-ui**: search no longer reflows canvas (flicker); directional arrow edges to show start→end
- **graph-ui**: recompute selection neighbors on view/payload rebuild (double-click ego); concentric radial layout for ego views
- **graph-ui**: header/dropdown gap, square dropdown items, search clear (x) button
- **graph**: qualify nested-class node-ids by outer class + capture attribute/subscript bases (recall)
- **graph-ui**: isolated (hidden) node early-returns from reducer — fixes startsWith crash on select (missing borderColor)
- **graph-ui**: crash-proof layout for tiny/edgeless ego (skip+sanitize NaN) + ErrorBoundary; collapsible sidebar & controls
- **graph**: disambiguate cross-module calls via the import graph (kills get/options/delete over-match), not a name list
- **graph**: builtin names (dict/list/isinstance args) aren't call/callback edges — via dir(builtins), not a curated list
- **graph**: only resolve unambiguous cross-module call/type names (kills false from_orm/get fan-in: 6802→2906 calls edges)
- **graph**: decorator calls (e.g. `@app`.get) are no longer counted as function callees (false calls edges)
- **graph-ui**: refit camera after view-transition morph so spread-out neighbors + labels stay on-screen
- **serve**: swallow client disconnect (BrokenPipeError) instead of dumping a traceback
- **graph-ui**: dim (not hide) on select + stop label flicker; O(n) overview & adjacency-BFS ego for large graphs
- **cli**: bare 'auditor' shows help and exits 0; reword positioning to 'deterministic codebase auditor'
- **graph-ui**: include full graph in payload so ego/cluster views aren't starved by node_cap
- **graph**: concept matches by member name + returns empty on no match
- **graph-ui**: clickable + hoverable labels; force-render labels; no overlay flash
- **graph**: readable hover labels (dark hover renderer)
- **graph**: full-bleed layout (CSS reset, no body margin)
- **graph**: more legible node labels (brighter/bold/larger)
- **graph**: don't set sigma-reserved 'type' node attr (click crash)
- **graph**: node_cap keeps top-ranked nodes, not alphabetical
- **graph**: god-concept splits fan-out (god object) from fan-in (bottleneck)
- **graph**: production callers no longer resolve calls to test definitions
- **graph**: use networkx greedy-modularity for clustering (min-label LPA collapsed to 1 cluster)
- **graph**: dedup nodes by id before persist (property getter/setter, overloads)
- **graph**: include node kind in neighbors() output (match spec shape)
- **graph**: Task-11 graph CLI ships only 'build' (query commands come in Task 12)
- **graph**: param-gate is_hof (no over-fire) + tighten extract type hints

### Refactor

- **graph**: resolve_structural -> StructuralResolver class
- **graph**: extract_file_facts -> FileExtractor class
- **database**: derive PK from column flags + declarative Index model
- **database**: make retry_on_locked decorator public (no leading underscore)
- **database**: _retry_on_locked decorator for schema init (drop thunk helper)
- **database**: derive findings INSERT columns from the schema (drop _FINDING_COLS constants)
- **database**: structured Column model for schema definitions
- **database**: Table.render method + tuple pk/index columns
- **database**: declarative Table model for schema (derive SCHEMA + cache set)
- **database**: auto-register table stores via __init_subclass__ (drop _STORES tuple)
- **database**: co-locate each table's DDL in its store (SCHEMA/CACHE_TABLES classvars)
- **database**: drop stuttering method prefixes (index.findings.cached, etc.)
- **database**: split per-table DB stores into auditor/database/ package
- **index**: migrate call sites to per-table store API; drop delegators
- **index**: extract per-table stores under IndexStore facade (delegators kept)
- **graph**: trim stopwords to structural-only (POC: domain nouns not load-bearing)
- **graph**: drop verb-synonym map (POC: 0 benefit); add snowballstemmer to [graph] extra

### Perf

- **graph-ui**: serve reuses the built graph (only rebuilds when missing or --rebuild) — fast relaunch
- **graph-ui**: adaptive ForceAtlas2 iterations + Barnes-Hut for deep/large ego graphs
- **index**: batch per-file cache reads + bulk inserts; build graph facts on cache-hit

## v0.2.1 (2026-06-16)

### Fix

- SEQUENTIAL-AWAITS skips ordered-sink writes to with-bound resources (false positive)

## v0.2.0 (2026-06-16)

### Feat

- close detection gaps — from-import sinks, aliased datetime, greenlet walrus/kwarg/partial-refresh, %/format raw-SQL, resolver submodule+__all__
- CalleeResolver follows re-exports (star/explicit/relative/absolute, bounded)
- engine discovers target env + warns when resolve_packages set but no env
- CalleeResolver resolves in-reach dependency modules from the target env
- find_site_packages — discover the scanned project's env from its root
- [tool.auditor] resolve_packages config (callee-resolver dependency reach)
- SA-GREENLET clears findings via resolver-proven cross-file refresh helpers
- greenlet refresh-effect extraction (direct + bulk-loop, unconditional-only)
- engine builds CalleeResolver per scan and threads it to the Python auditor
- AuditContext carries an optional resolver
- CalleeResolver — resolve calls to sibling-module defs (repo-local, cached)
- finding_detail MCP tool — recover full finding record (index, re-scan fallback)
- MCP scan/report detail knob (compact default, full back-compat, summary)
- json_reporter compact + summary modes (full stays default/back-compat)

### Fix

- nested async def await no longer leaks into outer NoAwaitBody; +39 obscure rule tests
- surface auditor warnings by default in the CLI (verbosity 0 = WARNING)
- cut false positives across 10 detectors + configurable cli_frameworks
- SA-GREENLET-ATTR-AFTER-COMMIT tracks reload/rebind (refresh, re-query, post-commit construction) + drops add_all collection FP
- PY-ASYNC-NO-AWAIT-BODY exempts route handlers + `@abstractmethod` (DRY is_route_handler→_util)
- shapes.py FP precision — PY-DEAD-SYMBOL skips side-effect registrations (call/subclass/decorator), PY-XFILE-DUP-FUNCTION ignores leading docstring

### Refactor

- extract _validate_detail helper + test report bad-detail

### Perf

- omit clean files + redundant counts from compact MCP scan payload (-51% tokens)

## v0.1.1 (2026-06-15)

### Feat

- add auditr/auditr-mcp console commands (auditor aliased) + bump to 0.1.1

## v0.1.0 (2026-06-15)

### Feat

- catch joblib.load (pickle RCE) + datetime.utcfromtimestamp (naive) — gaps found via manual audit
- framework rules — SA implicit-lazy-async/joined-collection, pytest mutable-fixture, pydantic v1-config, 3 React hook rules
- injectable --config-json config overrides (CLI scan/report/discover/config-show + MCP)
- scan --rule filter + did-you-mean rule suggestions (CLI + MCP)
- framework-aware SQLAlchemy rules (SA-*) + [tool.auditor.sqlalchemy] engine config
- PY-DEAD-SYMBOL — flag unused module-level private symbols/constants (repo-level)
- framework-aware structural pytest test rules (PY-TEST-*)
- skip git-ignored files + soft-skip migration dirs by default (scannable when targeted; --include-gitignored / respect_gitignore to override)
- replace flake8 # noqa with auditor-native # auditor: skip / skip-file (own namespace, no ruff collisions); rename respect_skips/--no-skips/no_skips
- PY-CONFIG-SCATTERED-SETTINGS rule — flag BaseSettings outside the settings home (transitive, config + auto-cohesion); relocate GlobalPaths to config.py
- persistent ignores (repo/file/line + rule_id) via CLI ignore sub-app and MCP tools
- relocate index to shared ~/.auditor db, partitioned by repo with FK-backed repo registry
- close obscure detection gaps (import aliases, argv reverse shells, setTimeout, interpreter pipes) with regression tests
- supply-chain detection, --baseline mode, cross-file method dedup
- secrets + cross-language malware detection for py/ts/bash
- malware category — Python detectors (obfuscated/remote exec, reverse shell, download-exec, miner, credential-access, encoded blob)
- parallel_sibling_min_group threshold (the group-size floor was hardcoded 2); TS reads config too
- PR/CI workflow — --since/--changed/--vs-base diff scoping, --fail-on gate, --min-severity
- --severity/-S filter to show only selected severities (CLI + MCP)
- PY-OOP-DUPLICATE-BLOCK — flag copy-pasted statement blocks within a file
- -v/-vv/-vvv loguru logging, summary-by-default scan output, clean errors on bad target
- respect noqa directives (line + file-level) with --no-noqa override
- add unawaited-coroutine, raise-without-from, naive-datetime rules
- close audit-skill gaps (items 11/13/15/17/18/22 + decorative-icon a11y) and add suggestions tier
- TS-REACT-ARRAY-INDEX-KEY (found via ground-truth audit of tailor: 21 real misses)
- pluggable design-system audits — declare your shell/primitives, opt into DS checks
- TS security (XSS) + a11y + size/complexity rules, complex fixtures, OOP cleanup
- TS-XFILE-DUP-JSX-BLOCK — recurring inline JSX sub-trees across files
- TS DRY/extraction rules (extractable hook/helper, parallel-sibling)
- TypeScript/React auditing (tree-sitter) — a11y + structure + cross-file DRY dedup
- redesigned html report (tree TOC, filters, beautified) + --exclude flag
- severity-ordered output, html report + --serve, --output, short flags
- --profile flag to override the profile per run (scan/report + MCP)
- token-efficient repo auditor (CLI + MCP, 48 detectors, async index, plugins)

### Fix

- release.sh confirms cleanly without a TTY (--yes for non-interactive)
- index repos name derives the repo basename (was empty for relative roots)
- soft-skip alembic migration dir variants (versions_legacy/versions_backup/manual_migrations)
- soft-skip no longer swallows test dirs named migrations (tests/migrations/)
- discover (CLI + MCP) honors [tool.auditor] exclude so it lists only what scan audits
- baseline counts fingerprint occurrences (multiset) so distinct findings sharing a snippet are each recorded and an added occurrence still surfaces — recorded==hidden, closes masking gap
- ignore add loads repo config so plugin rules validate without --force (entry-point/config/trusted-local); add -a, reword
- validate ignore-add rule_id (with --force/force escape hatch) and rules-list --category/--standard; clean errors instead of silent no-ops
- MCP report/manifest/scan raise a clean ToolError on missing/invalid inputs instead of a raw OSError traceback (surfaced by stress testing)
- two detector false positives surfaced by dogfooding noct — PY-SEC-HARDCODED-SECRET skips path/location values; SH-MAL-ANTIFORENSICS only flags real log-wiping not app-log redirects
- import-time-IO no longer double-reports chained I/O calls (requests.get(...).json())
- register a repo lazily on write so read-only/global index ops don't leave a placeholder row
- address codebase-review findings across cache, detectors, and CLI
- dispatch-ladder also catches guard-clause form (N sibling ifs on one discriminator)
- severity filter is -s (was -S, collided with -s/--serve); --serve is now long-only
- scan log header shows the target + root so a nested package dir isn't mistaken for another repo
- noqa honors only real comments (tokenize) so docstring examples don't suppress
- propagate noqa suppressed count through the index scan path
- retry WAL journal-mode switch on init to end parallel-writer lock race
- TS array-index-key allows composite keys; dup-import ignores import type
- multi-component FPs — exempt compound families + SCREAMING_CASE consts (found via tailor)
- dup-component requires >=3 distinct tags (all-div collide) — found via tailor audit
- dup-function precision (member/key names; data consts excluded) — found via tailor audit
- exclude generated TS (*.gen.ts/*.d.ts); parallel-sibling only on functions/components
- make SSRF taint-aware (only caller-derived URLs, not module constants)
- scope insecure-random to security contexts; exempt async generators from no-await-body
- open Windows browser for --serve on WSL

### Refactor

- remove dead [tool.auditor] include field (was wired to nothing); now errors instead of silent no-op
- read AUDITOR_HOME via a pydantic BaseSettings (GlobalPaths) instead of os.environ
- split cli.py into a cli/ package (one file per command)
- group Threshold into oop/size/dry/jsx sub-models with Field descriptions + ge=1 validation (clears its own FLAT-FIELD-MODEL)
- language-agnostic ParallelSiblingMixin — PY/TS inject candidates+token, share the group-and-flag algorithm
- dogfood cleanup — DRY git helper, simpler display filter, extract diff-ref resolution, mark snapshot JSON boundary
- split suggestions.py + DRY cli guards; make DRY/JSX thresholds config-tunable
- make security/oop detectors intentional (taint-aware SQL, context-aware bind/tempfile, pure-forward thin-wrapper), CLI spinner, worker stress tests
- ManifestEntry factory + ast_util, mirror test tree, README icon, dogfood inline-import guard
