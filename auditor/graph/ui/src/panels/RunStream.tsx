import { useState } from "react";
import type { LiveGraph } from "../api/useLiveGraph";
import { TEXT, THEME } from "../theme";
import { stream } from "./runs";
import Panel, { mono } from "./Panel";
import RunDetail from "./RunDetail";
import StreamRow, { COLUMNS, head } from "./StreamRow";
import { answered, Empty, Phases } from "./States";

/** The one control shape the stream's two disclosures share: a dashed pill with a count in it. */
function Chip({
  label,
  count,
  onClick,
}: {
  label: string;
  count: number;
  onClick?: () => void;
}) {
  const style: React.CSSProperties = {
    background: "transparent",
    border: `1px solid ${THEME.border}`,
    borderRadius: "999px",
    color: TEXT.label,
    fontSize: "10.5px",
    maxWidth: "100%",
    overflow: "hidden",
    padding: "3px 9px",
    textAlign: "left",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const body = (
    <>
      <span style={{ color: TEXT.body, fontWeight: 600 }}>{count}</span> {label}
    </>
  );
  if (!onClick) return <span style={style}>{body}</span>;
  return (
    <button type="button" onClick={onClick} className="state-retry" style={{ ...style, cursor: "pointer" }}>
      {body}
    </button>
  );
}

/** Spec 12.1's run stream, with `skipped` rows collapsed behind their reason (C11 and C12). */
export default function RunStream({ live }: { live: LiveGraph }) {
  const [open, setOpen] = useState<string | null>(null);
  const log = live.runs.data?.log;
  const rows = log?.runs ?? [];
  const { shown, collapsed, reasons } = stream(rows, live.showSkipped);
  // the server withholds skipped rows until asked, so its count is the only way to offer them
  const hidden = live.showSkipped ? collapsed.length : (log?.hidden_count ?? 0);
  return (
    <Panel
      title="Runs"
      testId="RunStream"
      trailing={
        shown.length > 0 ? (
          <span style={{ ...mono, color: TEXT.label }}>
            {log?.truncated ? `${shown.length} of ${log.run_count}` : shown.length}
          </span>
        ) : null
      }
    >
      <Phases state={live.runs} what="runs" onRetry={live.retry} />

      {answered(live.runs) && shown.length === 0 ? (
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
                {COLUMNS.map((column) => (
                  <th key={column.label} style={head}>
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <StreamRow
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

      {hidden > 0 || live.showSkipped ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
          <Chip
            label={live.showSkipped ? "skipped, hide them" : "skipped, show them"}
            count={hidden}
            onClick={() => live.setShowSkipped(!live.showSkipped)}
          />
          {[...reasons].map(([reason, count]) => (
            <Chip key={reason} label={`skipped: ${reason}`} count={count} />
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
