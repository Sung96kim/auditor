import { useState } from "react";
import type { LiveGraph } from "../api/useLiveGraph";
import type { RunRow } from "../api/types";
import { onEnterOrSpace } from "../a11y";
import { TEXT, THEME, TONE } from "../theme";
import { costLabel, duration, runTone, stream } from "./runs";
import Panel, { microLabel, mono } from "./Panel";
import RunnerMark from "./RunnerMark";
import RunDetail from "./RunDetail";
import { Empty, Failed, Loading, Reconnecting } from "./States";

const cell: React.CSSProperties = {
  color: TEXT.body,
  fontSize: "11px",
  padding: "4px 6px",
  textAlign: "left",
  whiteSpace: "nowrap",
};

const numeric: React.CSSProperties = {
  ...cell,
  fontFamily: mono.fontFamily,
  fontVariantNumeric: "tabular-nums",
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

/** Four of the ten columns are nullable on the wire: a `cli` run has no session and no checkout. */
function short(value: string | null, width: number): string {
  return value ? value.slice(0, width) : "-";
}

function Row({
  row,
  open,
  onOpen,
}: {
  row: RunRow;
  open: boolean;
  onOpen: (id: string) => void;
}) {
  const seconds = duration(row);
  const pick = () => onOpen(row.run_id);
  return (
    <tr
      className="run-row"
      tabIndex={0}
      onClick={pick}
      onKeyDown={onEnterOrSpace(pick)}
      style={{
        background: open ? "rgba(124, 124, 255, 0.14)" : undefined,
        boxShadow: open ? `inset 2px 0 0 ${THEME.accent}` : undefined,
        cursor: "pointer",
      }}
    >
      <td style={cell}>{row.trigger_kind}</td>
      <td style={cell}>{row.client}</td>
      <td style={cell}>{row.producer}</td>
      <td style={{ ...cell, lineHeight: 0 }}>
        <RunnerMark runner={row.runner} size={13} />
      </td>
      <td style={numeric}>{short(row.session_id, 8)}</td>
      <td style={numeric}>
        {row.branch ?? "-"}@{short(row.commit_sha, 7)}
      </td>
      <td style={cell}>{row.model ?? "-"}</td>
      <td style={numeric}>{costLabel(row)}</td>
      <td style={numeric}>{seconds === null ? "running" : `${seconds.toFixed(1)}s`}</td>
      <td style={{ ...cell, color: TONE[runTone(row.status)], fontWeight: 600 }}>{row.status}</td>
    </tr>
  );
}

/** Spec 12.1's run stream, with `skipped` rows collapsed behind their reason (C11 and C12). */
export default function RunStream({ live }: { live: LiveGraph }) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = live.runs.data?.log.runs ?? [];
  const { shown, reasons } = stream(rows);
  const answered = live.runs.phase === "ready" || live.runs.phase === "stale";
  return (
    <Panel
      title="Runs"
      testId="RunStream"
      trailing={
        shown.length > 0 ? <span style={{ ...mono, color: TEXT.label }}>{shown.length}</span> : null
      }
    >
      {live.runs.phase === "loading" ? <Loading what="runs" /> : null}
      {live.runs.phase === "error" ? (
        <Failed error={live.runs.error} onRetry={live.retry} />
      ) : null}
      {live.runs.phase === "stale" ? (
        <Reconnecting error={live.runs.error} onRetry={live.retry} />
      ) : null}

      {answered && shown.length === 0 ? (
        <Empty
          what="runs"
          hint="the observer has not started a refinement run for this repo yet"
        />
      ) : null}

      {shown.length > 0 ? (
        <div style={{ margin: "0 -14px", overflowX: "auto", padding: "0 14px" }}>
          <table style={{ borderCollapse: "collapse", width: "100%" }}>
            <thead>
              <tr>
                {COLUMNS.map((name) => (
                  <th
                    key={name}
                    style={{
                      ...cell,
                      ...microLabel,
                      borderBottom: `1px solid ${THEME.border}`,
                      paddingBottom: "6px",
                    }}
                  >
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <Row
                  key={row.run_id}
                  row={row}
                  open={row.run_id === open}
                  onOpen={setOpen}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {reasons.size > 0 ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
          {[...reasons].map(([reason, count]) => (
            <button
              key={reason}
              type="button"
              onClick={() => live.setShowSkipped(true)}
              className="state-retry"
              title={`Show the ${count} run${count === 1 ? "" : "s"} skipped: ${reason}`}
              style={{
                background: "transparent",
                border: `1px solid ${THEME.border}`,
                borderRadius: "999px",
                color: TEXT.label,
                cursor: "pointer",
                fontSize: "10.5px",
                maxWidth: "100%",
                overflow: "hidden",
                padding: "3px 9px",
                textAlign: "left",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              <span style={{ color: TEXT.body, fontWeight: 600 }}>{count}</span> skipped: {reason}
            </button>
          ))}
        </div>
      ) : null}

      {open ? (
        <RunDetail
          key={open}
          base={live.boot.base}
          repo={live.boot.repo}
          runId={open}
          onClose={() => setOpen(null)}
        />
      ) : null}
    </Panel>
  );
}
