import { useFetchOnce } from "../api/useFetchOnce";
import type { RunDetailView } from "../api/types";
import { TEXT, THEME } from "../theme";
import { accepted, rejected } from "./runs";
import RefinementGroup from "./RefinementGroup";
import { Clipped, Field, microLabel, mono, nested, wrapped } from "./Panel";
import { Phases } from "./States";
import { trialLine } from "./trials";

const box: React.CSSProperties = {
  ...nested,
  borderLeft: `2px solid ${THEME.accent}`,
  color: TEXT.body,
  display: "flex",
  flexDirection: "column",
  fontSize: "11.5px",
  gap: "9px",
  padding: "10px 12px 12px",
};

/** Verbatim text the runner wrote or read: penned in, wrapped, and capped so it cannot run away. */
const verbatim: React.CSSProperties = {
  ...wrapped,
  background: THEME.bgPanel,
  border: `1px solid ${THEME.border}`,
  borderRadius: "6px",
  margin: 0,
  maxHeight: "120px",
  overflowY: "auto",
  padding: "7px 8px",
  whiteSpace: "pre-wrap",
};

export interface RunDetailProps {
  base: string;
  repo: string;
  runId: string;
  onClose: () => void;
}

/** Spec 12.1's C13. Fetched on a row click, never on the 3 s cycle (P3).
 *
 * No reconnect arm either: `RunStream` mounts this keyed on the open run, so choosing another
 * row remounts it with nothing held. That is the right behaviour, since run A's prompt under a
 * banner headed run B would be worse, and it leaves the reconnect state unreachable here.
 */
export default function RunDetail({ base, repo, runId, onClose }: RunDetailProps) {
  const { state, retry } = useFetchOnce<RunDetailView>(
    `${base}api/runs/${runId}?${new URLSearchParams({ repo })}`,
  );
  const view = state.data;
  return (
    <div style={box} data-testid="RunDetail">
      <div style={{ alignItems: "center", display: "flex", gap: "8px" }}>
        <span style={microLabel}>Run</span>
        <span style={{ ...mono, color: TEXT.strong }}>
          <Clipped value={runId} width={8} />
        </span>
        <button
          type="button"
          aria-label="Close run detail"
          onClick={onClose}
          className="state-retry"
          style={{
            background: "transparent",
            border: `1px solid ${THEME.border}`,
            borderRadius: "6px",
            color: TEXT.label,
            cursor: "pointer",
            fontSize: "13px",
            lineHeight: 1,
            marginLeft: "auto",
            padding: "3px 7px",
          }}
        >
          &#215;
        </button>
      </div>

      <Phases state={state} what="this run" onRetry={retry} reconnects={false} />

      {view ? (
        <>
          <Field title="Prompt">
            <pre style={verbatim}>{view.prompt || "no prompt recorded"}</pre>
          </Field>

          <Field title="Tool trace">
            {view.tool_trace.length === 0 ? (
              <span style={{ color: TEXT.label }}>no tool calls recorded</span>
            ) : (
              view.tool_trace.map((call, i) => (
                <span key={`${call.tool}-${call.ts}-${i}`} style={wrapped}>
                  <span style={{ color: TEXT.strong }}>{call.tool}</span> {call.detail}
                </span>
              ))
            )}
          </Field>

          <RefinementGroup title="Accepted changes" rows={accepted(view.refinements)} />
          <RefinementGroup title="Rejected proposals" rows={rejected(view.refinements)} />

          <Field title="Tuning trials">
            {view.trials.length === 0 ? (
              <span style={{ color: TEXT.label }}>no knob change was proposed on this run</span>
            ) : (
              view.trials.map((trial) => (
                <span key={trial.tuning_id} style={wrapped}>
                  {trialLine(trial)}
                </span>
              ))
            )}
          </Field>

          <Field title="Assessment">
            <span style={wrapped}>
              {view.assessment?.verdict
                ? `${view.assessment.verdict.decision}: ${view.assessment.verdict.reason}`
                : "no assessment recorded"}
            </span>
          </Field>
        </>
      ) : null}
    </div>
  );
}
