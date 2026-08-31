import { useState } from "react";
import type { LiveGraph } from "../api/useLiveGraph";
import type { RunRow } from "../api/types";
import { THEME } from "../theme";
import { costLabel, duration, stream } from "./runs";
import RunnerMark from "./RunnerMark";
import RunDetail from "./RunDetail";
import { Empty, Failed, Loading, Reconnecting } from "./States";

const card: React.CSSProperties = {
  padding: "12px 14px",
  borderRadius: "10px",
  border: `1px solid ${THEME.border}`,
  backgroundColor: THEME.bgPanel,
  display: "flex",
  flexDirection: "column",
  gap: "8px",
};

const header: React.CSSProperties = {
  fontSize: "10.5px",
  fontWeight: 700,
  letterSpacing: "0.09em",
  color: "#64748b",
  textTransform: "uppercase",
};

const cell: React.CSSProperties = {
  padding: "3px 5px",
  fontSize: "11px",
  color: "#94a3b8",
  whiteSpace: "nowrap",
  textAlign: "left",
};

/** Spec 12.1's ten columns, in the order the spec names them. */
const COLUMNS = [
  "trigger",
  "client",
  "producer",
  "runner",
  "session",
  "branch@commit",
  "model",
  "cost",
  "duration",
  "status",
];

function Row({ row, onOpen }: { row: RunRow; onOpen: (id: string) => void }) {
  const seconds = duration(row);
  return (
    <tr onClick={() => onOpen(row.run_id)} style={{ cursor: "pointer" }}>
      <td style={cell}>{row.trigger_kind}</td>
      <td style={cell}>{row.client}</td>
      <td style={cell}>{row.producer}</td>
      <td style={cell}>
        <RunnerMark runner={row.runner} />
      </td>
      <td style={cell}>{row.session_id.slice(0, 8)}</td>
      <td style={cell}>
        {row.branch}@{row.commit_sha.slice(0, 7)}
      </td>
      <td style={cell}>{row.model}</td>
      <td style={cell}>{costLabel(row)}</td>
      <td style={cell}>{seconds === null ? "running" : `${seconds.toFixed(1)}s`}</td>
      <td style={cell}>{row.status}</td>
    </tr>
  );
}

/** Spec 12.1's run stream, with `skipped` rows collapsed behind their reason (C11 and C12). */
export default function RunStream({ live }: { live: LiveGraph }) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = live.runs.data?.log.runs ?? [];
  const { shown, reasons } = stream(rows);
  return (
    <div style={card} data-testid="RunStream">
      <span style={header}>Runs</span>
      {live.runs.phase === "loading" ? <Loading what="runs" /> : null}
      {live.runs.phase === "error" ? (
        <Failed error={live.runs.error} onRetry={live.retry} />
      ) : null}
      {live.runs.phase === "stale" ? (
        <Reconnecting error={live.runs.error} onRetry={live.retry} />
      ) : null}

      {live.runs.phase !== "loading" && shown.length === 0 ? (
        <Empty
          what="runs"
          hint="the observer has not started a refinement run for this repo yet"
        />
      ) : null}

      {shown.length > 0 ? (
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%" }}>
            <thead>
              <tr>
                {COLUMNS.map((name) => (
                  <th key={name} style={{ ...cell, ...header }}>
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <Row key={row.run_id} row={row} onOpen={setOpen} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {[...reasons].map(([reason, count]) => (
        <button
          key={reason}
          type="button"
          onClick={() => live.setShowSkipped(true)}
          style={{
            background: "transparent",
            border: `1px dashed ${THEME.border}`,
            borderRadius: "7px",
            color: "#64748b",
            cursor: "pointer",
            fontSize: "11px",
            padding: "4px 8px",
            textAlign: "left",
          }}
        >
          {count} skipped: {reason}
        </button>
      ))}

      {open ? (
        <RunDetail
          base={live.boot.base}
          repo={live.boot.repo}
          runId={open}
          onClose={() => setOpen(null)}
        />
      ) : null}
    </div>
  );
}
