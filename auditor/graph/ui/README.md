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

**Rebuild `dist/index.html` after any UI changes before committing.**

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
