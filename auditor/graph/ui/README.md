# Auditor Graph UI

Vite + React + TypeScript + sigma.js visualization for the auditor semantic graph.

## IMPORTANT: pnpm ONLY

This project uses **pnpm exclusively**. Never use `npm`, `npx`, `yarn`, or `bun`.

## Setup

```bash
pnpm install
```

## Development

```bash
pnpm dev
```

Opens a dev server with hot-module reloading. Falls back to `sample.ts` fixture data when `window.__AUDITOR_GRAPH__` is not set.

## Build

```bash
pnpm build
```

Produces a **single self-contained** `dist/index.html` (all JS and CSS inlined via `vite-plugin-singlefile`). This file is committed to the repo and shipped in the Python wheel so `serve.py` can return it.

**Rebuild `dist/index.html` after any UI changes before committing.** From the repo root, the
whole cycle is five commands, always through pnpm and never npm, npx, yarn or bun:

```bash
pnpm --dir auditor/graph/ui install --frozen-lockfile   # pnpm only, never npm or yarn
pnpm --dir auditor/graph/ui typecheck                   # tsc --noEmit
pnpm --dir auditor/graph/ui test                        # vitest run
pnpm --dir auditor/graph/ui build                       # rewrites the committed dist/index.html
uv run python -m auditor.graph.ui_inputs --write        # restamps dist/inputs.sha256
```

`tests/graph/test_ui_bundle.py` fails until the last two have both been run: the build rewrites
the artifact and the stamp records the digest of every input it was built from.

CI compares the committed `dist/index.html` byte for byte against a fresh rebuild. That assumes a
rebuild reproduces across machines, which has never been measured here, so `engines.node` pins the
runtime to one major and keeps the minifier's identifier naming stable. If the two ever disagree,
the pytest stamp above is the authoritative gate.

## Type-check

```bash
pnpm typecheck
```

## Tests

```bash
pnpm test
```

## Architecture

- `src/types.ts` — TypeScript mirror of the `build_payload` Python contract (`GraphPayload`, `GNode`, `GEdge`, `GCluster`)
- `src/theme.ts` — Reference colors (`NODE_COLOR`, `THEME`)
- `src/sample.ts` — Small fixture `GraphPayload` for local dev
- `src/App.tsx` — Root component; reads `window.__AUDITOR_GRAPH__` or falls back to `sample`
- `src/main.tsx` — React root entry point
