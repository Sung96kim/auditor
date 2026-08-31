# Auditor Graph UI

Vite + React + TypeScript + sigma.js visualization for the auditor semantic graph.

## IMPORTANT: pnpm ONLY

This project uses pnpm exclusively. Never use `npm`, `npx`, `yarn`, or `bun`.

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

Produces a single self-contained `dist/index.html`, with all JS and CSS inlined by
`vite-plugin-singlefile`. It is committed to the repo and shipped in the Python wheel, so
`serve.py` can return it.

Rebuild `dist/index.html` after any UI change, before committing. From the repo root the whole
cycle is five commands, always through pnpm and never npm, npx, yarn or bun:

```bash
pnpm --dir auditor/graph/ui install --frozen-lockfile   # pnpm only, never npm or yarn
pnpm --dir auditor/graph/ui typecheck                   # tsc --noEmit
pnpm --dir auditor/graph/ui test                        # vitest run
pnpm --dir auditor/graph/ui build                       # rewrites the committed dist/index.html
uv run python -m auditor.graph.ui_inputs --write        # restamps dist/inputs.sha256
```

`tests/graph/test_ui_bundle.py` fails until the last two have both been run: the build rewrites
the artifact and the stamp records the digest of every input it was built from.

CI compares the committed `dist/` byte for byte against a fresh rebuild. That assumes a rebuild
reproduces across machines, which has never been measured here, so the `ui` job's
`actions/setup-node` pins `node-version: 22`. `engines.node` in `package.json` is advisory only:
pnpm warns on an unsatisfiable range and exits 0, there is no `.npmrc`, and `engine-strict` is
unset. If a rebuild ever disagrees with the committed bytes, the pytest stamp above is the
authoritative gate.

`vite.config.ts` sets `emptyOutDir: false`, because `dist/inputs.sha256` is committed and lives in
the output directory; vite's default wipes it on every build. It also names `vitest.setup.ts`,
which stubs the two WebGL constructors and `matchMedia` that jsdom does not have, so a module
importing sigma can at least be loaded and held to something under test.

## Type-check

```bash
pnpm typecheck
```

## Tests

```bash
pnpm test
```

## Architecture

- `src/`, the app's root. `main.tsx` mounts it, `App.tsx` reads `window.__AUDITOR_GRAPH__` or
  falls back to `sample.ts`, `types.ts` mirrors the `build_payload` contract, `theme.ts` holds the
  colour tokens and `a11y.ts` the shared key handler.
- `src/api/`, the daemon's wire. `bootstrap.ts` reads the injected flag, `types.ts` declares every
  shape the page reads, `client.ts` is one conditional GET, `poll.ts` the four-state machine and
  `useLiveGraph.ts` the 3 s cycle.
- `src/components/`, the graph itself. The sigma canvas, the 3D and text views, the explorer, the
  detail panel and the top bar.
- `src/graph/`, pure functions over the payload. Building, filtering, selection, ranking.
- `src/panels/`, the live column. The observer chrome, the run stream and its detail, the
  refinement list, the runner marks and the four shared state components.
- `src/flow/`, the flow walk. The panel, and the layout that flattens and places it.
