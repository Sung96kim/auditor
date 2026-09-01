import type { ReactNode } from "react";
import type { RefinementRow } from "../api/types";
import { TEXT } from "../theme";
import { refinementTarget, type Targeted } from "./runs";
import { mono } from "./Panel";

/** One refinement as both surfaces draw it: its tier, its kind, and what it points at.
 *
 * Two views rendered their own row and neither drew the kind, so an `add_edge` and the
 * `confirm_edge` on the same pair were the same line, and a cluster relabel was a bare dash.
 */
export type Refined = Targeted & Pick<RefinementRow, "refinement_id" | "tier" | "status">;

/** `trailing` is the refinement list's drift mark, which run detail has no column for. */
export default function RefinementLine({
  row,
  trailing,
}: {
  row: Refined;
  trailing?: ReactNode;
}) {
  return (
    <span style={{ ...mono, overflowWrap: "anywhere" }}>
      <span style={{ color: TEXT.label }}>[{row.tier}]</span> {row.kind}{" "}
      {refinementTarget(row)}
      {trailing}
    </span>
  );
}
