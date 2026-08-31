import { useEffect, useState } from "react";
import { getJson } from "../api/client";
import { failed, initial, received, type PollState } from "../api/poll";
import type { RunDetailView } from "../api/types";
import { TEXT, THEME } from "../theme";
import { accepted, rejected } from "./runs";
import { block, microLabel, mono, nested } from "./Panel";
import { Failed, Loading } from "./States";

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
  ...mono,
  background: THEME.bgPanel,
  border: `1px solid ${THEME.border}`,
  borderRadius: "6px",
  margin: 0,
  maxHeight: "120px",
  overflowWrap: "anywhere",
  overflowY: "auto",
  padding: "7px 8px",
  whiteSpace: "pre-wrap",
};

const line: React.CSSProperties = { ...mono, overflowWrap: "anywhere" };

interface Refined {
  refinement_id: string;
  tier: string;
  from_dst: string | null;
  dst: string | null;
  node_id: string | null;
  status: string;
}

/** What the row moved, or what it is about when it moved no edge: never a pair of dashes. */
function moved(row: Refined): string {
  if (row.from_dst === null && row.dst === null) return row.node_id ?? "no target recorded";
  return `${row.from_dst ?? "-"} to ${row.dst ?? "-"}`;
}

/** One labelled section, ruled off from the one above so six headings are not one grey wall. */
function Field({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ ...block, borderTop: `1px solid ${THEME.border}`, paddingTop: "9px" }}>
      <span style={microLabel}>{title}</span>
      {children}
    </div>
  );
}

function Group({ title, rows }: { title: string; rows: Refined[] }) {
  return (
    <Field title={title}>
      {rows.length === 0 ? (
        <span style={{ color: TEXT.label }}>none</span>
      ) : (
        rows.map((row) => (
          <span key={row.refinement_id} style={line}>
            <span style={{ color: TEXT.label }}>[{row.tier}]</span> {moved(row)}
          </span>
        ))
      )}
    </Field>
  );
}

export interface RunDetailProps {
  base: string;
  repo: string;
  runId: string;
  onClose: () => void;
}

/** Spec 12.1's C13. Fetched on a row click, never on the 3 s cycle (P3). */
export default function RunDetail({ base, repo, runId, onClose }: RunDetailProps) {
  const [state, setState] = useState<PollState<RunDetailView>>(() =>
    initial<RunDetailView>(),
  );

  useEffect(() => {
    let alive = true;
    const query = new URLSearchParams({ repo });
    getJson<RunDetailView>(`${base}api/runs/${runId}?${query}`, "")
      .then((got) => {
        if (alive) setState((prev) => received(prev, got.value));
      })
      .catch((err) => {
        if (alive) setState((prev) => failed(prev, String(err)));
      });
    return () => {
      alive = false;
    };
  }, [base, repo, runId]);

  const view = state.data;
  return (
    <div style={box} data-testid="RunDetail">
      <div style={{ alignItems: "center", display: "flex", gap: "8px" }}>
        <span style={microLabel}>Run</span>
        <span style={{ ...mono, color: TEXT.strong }}>{runId.slice(0, 8)}</span>
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

      {state.phase === "loading" ? <Loading what="this run" /> : null}
      {state.phase === "error" ? <Failed error={state.error} onRetry={onClose} /> : null}

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
                <span key={`${call.tool}-${call.ts}-${i}`} style={line}>
                  <span style={{ color: TEXT.strong }}>{call.tool}</span> {call.detail}
                </span>
              ))
            )}
          </Field>

          <Group title="Accepted changes" rows={accepted(view.refinements)} />
          <Group title="Rejected proposals" rows={rejected(view.refinements)} />

          <Field title="Tuning trials">
            {view.trials.length === 0 ? (
              <span style={{ color: TEXT.label }}>none, S11 is what writes a tuning row</span>
            ) : (
              view.trials.map((trial) => (
                <span key={trial.tuning_id} style={line}>
                  {trial.key} {trial.status}
                </span>
              ))
            )}
          </Field>

          <Field title="Assessment">
            <span style={line}>
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
