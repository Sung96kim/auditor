import type { ReactNode } from "react";
import { onEnterOrSpace } from "../a11y";
import type { RunRow } from "../api/types";
import { TEXT, THEME, TONE } from "../theme";
import { costLabel, durationLabel, runTone } from "./runs";
import { microLabel, mono } from "./Panel";
import RunnerMark from "./RunnerMark";

export const cell: React.CSSProperties = {
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

export const head: React.CSSProperties = {
  ...cell,
  ...microLabel,
  borderBottom: `1px solid ${THEME.border}`,
  paddingBottom: "6px",
};

/** Four of the ten columns are nullable on the wire: a `cli` run has no session and no checkout. */
function short(value: string | null, width: number): string {
  return value ? value.slice(0, width) : "-";
}

export interface Column {
  label: string;
  style: React.CSSProperties;
  cell: (row: RunRow) => ReactNode;
}

/** Spec 12.1's ten columns, in the order the spec names them, declared once.
 *
 * One list rather than a `<th>` list beside a `<td>` list: two orderings of the same ten columns
 * misalign the whole table the first time one of them gains a row.
 */
export const COLUMNS: Column[] = [
  { label: "trigger", style: cell, cell: (row) => row.trigger_kind },
  { label: "client", style: cell, cell: (row) => row.client },
  { label: "producer", style: cell, cell: (row) => row.producer },
  {
    label: "runner",
    style: { ...cell, lineHeight: 0 },
    cell: (row) => <RunnerMark runner={row.runner} size={13} />,
  },
  { label: "session", style: numeric, cell: (row) => short(row.session_id, 8) },
  {
    label: "branch@commit",
    style: numeric,
    cell: (row) => `${row.branch ?? "-"}@${short(row.commit_sha, 7)}`,
  },
  { label: "model", style: cell, cell: (row) => row.model ?? "-" },
  { label: "cost", style: numeric, cell: (row) => costLabel(row) },
  { label: "duration", style: numeric, cell: (row) => durationLabel(row) },
  {
    label: "status",
    style: cell,
    cell: (row) => (
      <span style={{ color: TONE[runTone(row.status)], fontWeight: 600 }}>{row.status}</span>
    ),
  },
];

export interface StreamRowProps {
  row: RunRow;
  open: boolean;
  onOpen: (id: string) => void;
}

/** One run in the stream: focusable, openable from the keyboard, and marked while it is open. */
export default function StreamRow({ row, open, onOpen }: StreamRowProps) {
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
      {COLUMNS.map((column) => (
        <td key={column.label} style={column.style}>
          {column.cell(row)}
        </td>
      ))}
    </tr>
  );
}
