import type { PollState } from "../api/poll";
import { TEXT, THEME, TONE, TONE_WASH } from "../theme";
import { nested } from "./Panel";

type Tone = keyof typeof TONE;

const box: React.CSSProperties = {
  ...nested,
  display: "flex",
  flexDirection: "column",
  gap: "6px",
  fontSize: "12px",
  lineHeight: 1.45,
  padding: "10px 12px",
};

/** The tone a state box wears: a tinted wash and a border of the same hue, never a bare outline. */
function toned(tone: Tone): React.CSSProperties {
  return { ...box, background: TONE_WASH[tone], borderColor: `${TONE[tone]}55` };
}

const title: React.CSSProperties = { fontSize: "12.5px", fontWeight: 600 };

/** `String(err)` spells a thrown Error `Error: ...`; the panel shows the reason, not the class. */
export function reason(error: string): string {
  return error.replace(/^(?:[A-Za-z]*Error):\s*/, "").trim();
}

function Retry({ label, tone, onClick }: { label: string; tone: Tone; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="state-retry"
      style={{
        alignSelf: "flex-start",
        background: "transparent",
        border: `1px solid ${TONE[tone]}66`,
        borderRadius: "7px",
        color: TONE[tone],
        cursor: "pointer",
        fontSize: "11px",
        fontWeight: 600,
        padding: "3px 10px",
      }}
    >
      {label}
    </button>
  );
}

/** Nothing to show, and that is the answer: an empty ledger is not a failure. */
export function Empty({ what, hint }: { what: string; hint?: string }) {
  return (
    <div style={box} role="status">
      <span style={{ ...title, color: TEXT.strong }}>No {what} yet</span>
      {hint ? <span style={{ color: TEXT.body }}>{hint}</span> : null}
    </div>
  );
}

/** The first poll has not answered. Bounded by the reconnect state, so it cannot stick. */
export function Loading({ what }: { what: string }) {
  return (
    <div style={{ ...box, gap: "8px" }} role="status" aria-live="polite">
      <span style={{ color: TEXT.body }}>Loading {what}</span>
      <span
        aria-hidden="true"
        style={{
          background: THEME.bgPanel,
          borderRadius: "999px",
          display: "block",
          height: "4px",
          overflow: "hidden",
        }}
      >
        <span className="state-track" />
      </span>
    </div>
  );
}

/** A poll failed over data we already have: keep drawing it and say the page went stale. */
export function Reconnecting({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div style={toned("warn")} role="status">
      <span style={{ ...title, color: TONE.warn }}>Reconnecting to the observer</span>
      <span style={{ color: TEXT.body }}>{reason(error)}</span>
      <Retry label="Retry now" tone="warn" onClick={onRetry} />
    </div>
  );
}

/** The daemon answered and declined. No retry is offered, because none of them can succeed:
 * the request is what it turned down, and the reader has to change it or go back. */
export function Refused({ error }: { error: string }) {
  return (
    <div style={toned("bad")} role="alert">
      <span style={{ ...title, color: TONE.bad }}>The observer refused this request</span>
      <span style={{ color: TEXT.body }}>{reason(error)}</span>
    </div>
  );
}

/** The first poll failed, so there is nothing to keep drawing. */
export function Failed({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div style={toned("bad")} role="alert">
      <span style={{ ...title, color: TONE.bad }}>Could not reach the observer</span>
      <span style={{ color: TEXT.body }}>{reason(error)}</span>
      <Retry label="Retry" tone="bad" onClick={onRetry} />
    </div>
  );
}

export interface PhasesProps {
  state: PollState<unknown>;
  /** what the surface is waiting for, as `Loading` says it: "runs", "the daemon", "this run".
   *
   * `undefined` where the first poll is not this surface's to draw, which is the whole switch for
   * that arm. Required, so that leaving it off is a typecheck error and not a blank panel. */
  what: string | undefined;
  onRetry: () => void;
  /** false where `stale` cannot reach the call site: a URL fixed for the life of the page, or a
   * guard above it that admits only the two failure phases. */
  reconnects?: boolean;
}

/** The ladder from a poll's phase to the box that says so, in one place rather than six.
 *
 * Every arm this returns is one of the five above, and the EMPTY arm is not here: only the panel
 * knows whether an answer it holds is empty, and each says it in its own words.
 */
export function Phases({ state, what, onRetry, reconnects = true }: PhasesProps) {
  if (state.phase === "loading") return what === undefined ? null : <Loading what={what} />;
  if (state.phase === "refused") return <Refused error={state.error} />;
  if (state.phase === "error") return <Failed error={state.error} onRetry={onRetry} />;
  if (state.phase === "stale")
    return reconnects ? <Reconnecting error={state.error} onRetry={onRetry} /> : null;
  if (state.phase === "ready") return null;
  // a sixth Phase is one typecheck error here, and a stale build blanks rather than leaks it
  state.phase satisfies never;
  return null;
}
