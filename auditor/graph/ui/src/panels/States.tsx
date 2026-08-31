import { THEME } from "../theme";

const box: React.CSSProperties = {
  padding: "18px 14px",
  borderRadius: "8px",
  border: `1px solid ${THEME.border}`,
  color: "#94a3b8",
  fontSize: "12.5px",
  lineHeight: 1.5,
};

/** Nothing to show, and that is the answer: an empty ledger is not a failure. */
export function Empty({ what, hint }: { what: string; hint?: string }) {
  return (
    <div style={box} role="status">
      <div style={{ color: "#cbd5e1", fontWeight: 600 }}>No {what} yet</div>
      {hint ? <div style={{ marginTop: "4px" }}>{hint}</div> : null}
    </div>
  );
}

/** The first poll has not answered. Bounded by the reconnect state, so it cannot stick. */
export function Loading({ what }: { what: string }) {
  return (
    <div style={box} role="status" aria-live="polite">
      Loading {what}
    </div>
  );
}

/** A poll failed over data we already have: keep drawing it and say the page went stale. */
export function Reconnecting({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div style={{ ...box, borderColor: "#a16207", color: "#fbbf24" }} role="status">
      <div>Reconnecting to the observer. {error}</div>
      <button type="button" onClick={onRetry} style={{ marginTop: "6px" }}>
        Retry now
      </button>
    </div>
  );
}

/** The first poll failed, so there is nothing to keep drawing. */
export function Failed({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div style={{ ...box, borderColor: "#b91c1c", color: "#fca5a5" }} role="alert">
      <div>Could not reach the observer. {error}</div>
      <button type="button" onClick={onRetry} style={{ marginTop: "6px" }}>
        Retry
      </button>
    </div>
  );
}
