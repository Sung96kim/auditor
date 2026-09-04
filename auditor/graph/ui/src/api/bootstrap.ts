/** The daemon's second injected global. Absent under `graph serve`, which keeps the page static. */
export interface Bootstrap {
  live: boolean;
  base: string;
  repo: string;
}

export const STATIC: Bootstrap = { live: false, base: "", repo: "" };

type Injected = { __AUDITOR_OBSERVER__?: Partial<Bootstrap> };

// declared here rather than in `App.tsx`, so `readBootstrap(window)` typechecks at every call
// site without a second module having to know the global exists
declare global {
  interface Window {
    __AUDITOR_OBSERVER__?: Partial<Bootstrap>;
  }
}

/** Read the flag at first paint: no probe, no 404 race, and no doomed request under `graph serve`. */
export function readBootstrap(win: Injected): Bootstrap {
  const raw = win.__AUDITOR_OBSERVER__;
  if (!raw || raw.live !== true) return STATIC;
  return { live: true, base: raw.base ?? "/", repo: raw.repo ?? "" };
}
