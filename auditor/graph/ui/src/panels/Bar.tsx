import { TEXT, THEME, TONE } from "../theme";
import type { Meter } from "./chrome";
import { block, microLabel, mono } from "./Panel";

const METER_TONE: Record<Meter["tone"], string> = {
  ok: TONE.busy,
  low: TONE.warn,
  spent: TONE.bad,
};

/** A track with no reading is hatched, so "not published yet" cannot be misread as "nothing left". */
const UNKNOWN_TRACK =
  "repeating-linear-gradient(115deg, rgba(122,139,163,0.22) 0 4px, transparent 4px 8px)";

/** One labelled bar. `known` is false for a repo whose loop has not published a reading yet. */
export default function Bar({ title, meter }: { title: string; meter: Meter }) {
  const pct = Math.round(meter.fill * 100);
  return (
    <div style={{ ...block, gap: "7px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "8px" }}>
        <span style={microLabel}>{title}</span>
        <span style={{ ...mono, color: meter.known ? TEXT.body : TEXT.label }}>{meter.label}</span>
      </div>
      <div
        aria-label={meter.known ? title : undefined}
        aria-valuenow={meter.known ? pct : undefined}
        aria-valuemin={meter.known ? 0 : undefined}
        aria-valuemax={meter.known ? 100 : undefined}
        role={meter.known ? "progressbar" : undefined}
        style={{
          background: meter.known ? THEME.bgElevated : UNKNOWN_TRACK,
          borderRadius: "999px",
          height: "5px",
          overflow: "hidden",
        }}
      >
        {meter.known ? (
          <div
            style={{
              background: METER_TONE[meter.tone],
              borderRadius: "999px",
              height: "100%",
              transition: "width 320ms cubic-bezier(0.22, 1, 0.36, 1), background 200ms ease",
              width: pct === 0 ? "0" : `max(4px, ${pct}%)`,
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
