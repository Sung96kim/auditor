import type { RefinementRow } from "../api/types";
import { TEXT } from "../theme";
import { Field, mono } from "./Panel";

/** The five columns a run detail row draws, taken from the wire shape rather than restated. */
export type Refined = Pick<
  RefinementRow,
  "refinement_id" | "tier" | "from_dst" | "dst" | "node_id" | "status"
>;

const line: React.CSSProperties = { ...mono, overflowWrap: "anywhere" };

/** What the row moved, or what it is about when it moved no edge: never a pair of dashes. */
export function moved(row: Refined): string {
  if (row.from_dst === null && row.dst === null) return row.node_id ?? "no target recorded";
  return `${row.from_dst ?? "-"} to ${row.dst ?? "-"}`;
}

/** One of run detail's two refinement lists, with its own zero rather than a missing heading. */
export default function RefinementGroup({
  title,
  rows,
}: {
  title: string;
  rows: Refined[];
}) {
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
