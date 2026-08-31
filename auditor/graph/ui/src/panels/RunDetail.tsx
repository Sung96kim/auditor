import { useEffect, useState } from "react";
import { getJson } from "../api/client";
import { failed, initial, received, type PollState } from "../api/poll";
import type { RunDetailView } from "../api/types";
import { THEME } from "../theme";
import { accepted, rejected } from "./runs";
import { Empty, Failed, Loading } from "./States";

const box: React.CSSProperties = {
  padding: "10px 12px",
  borderRadius: "8px",
  border: `1px solid ${THEME.border}`,
  backgroundColor: THEME.bgElevated,
  display: "flex",
  flexDirection: "column",
  gap: "8px",
  fontSize: "11.5px",
  color: "#94a3b8",
};

const header: React.CSSProperties = {
  fontSize: "10.5px",
  fontWeight: 700,
  letterSpacing: "0.09em",
  color: "#64748b",
  textTransform: "uppercase",
};

interface Refined {
  refinement_id: string;
  tier: string;
  from_dst: string | null;
  dst: string | null;
  status: string;
}

function Group({ title, rows }: { title: string; rows: Refined[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
      <span style={header}>{title}</span>
      {rows.length === 0 ? (
        <span style={{ color: "#475569" }}>none</span>
      ) : (
        rows.map((row) => (
          <span key={row.refinement_id} style={{ fontFamily: "monospace" }}>
            [{row.tier}] {row.from_dst ?? "-"} to {row.dst ?? "-"}
          </span>
        ))
      )}
    </div>
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
      <div style={{ display: "flex", justifyContent: "space-between", gap: "8px" }}>
        <span style={header}>Run {runId.slice(0, 8)}</span>
        <button type="button" onClick={onClose} style={{ background: "transparent", border: "none", color: "#64748b", cursor: "pointer" }}>
          close
        </button>
      </div>

      {state.phase === "loading" ? <Loading what="this run" /> : null}
      {state.phase === "error" ? <Failed error={state.error} onRetry={onClose} /> : null}

      {view ? (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
            <span style={header}>Prompt</span>
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: "11px" }}>
              {view.prompt || "no prompt recorded"}
            </pre>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
            <span style={header}>Tool trace</span>
            {view.tool_trace.length === 0 ? (
              <span style={{ color: "#475569" }}>no tool calls recorded</span>
            ) : (
              view.tool_trace.map((call, i) => (
                <span key={`${call.tool}-${call.ts}-${i}`} style={{ fontFamily: "monospace" }}>
                  {call.tool} {call.detail}
                </span>
              ))
            )}
          </div>

          <Group title="Accepted changes" rows={accepted(view.refinements)} />
          <Group title="Rejected proposals" rows={rejected(view.refinements)} />

          <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
            <span style={header}>Tuning trials</span>
            {view.trials.length === 0 ? (
              <Empty what="tuning trials" hint="S11 is what writes a tuning row" />
            ) : (
              view.trials.map((trial) => (
                <span key={trial.tuning_id} style={{ fontFamily: "monospace" }}>
                  {trial.key} {trial.status}
                </span>
              ))
            )}
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
            <span style={header}>Assessment</span>
            <span style={{ fontFamily: "monospace" }}>
              {view.assessment?.verdict
                ? `${view.assessment.verdict.decision}: ${view.assessment.verdict.reason}`
                : "no assessment recorded"}
            </span>
          </div>
        </>
      ) : null}
    </div>
  );
}
