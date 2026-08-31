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
