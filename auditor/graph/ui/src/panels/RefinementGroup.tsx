import { TEXT } from "../theme";
import { Field } from "./Panel";
import RefinementLine, { type Refined } from "./RefinementLine";

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
        rows.map((row) => <RefinementLine key={row.refinement_id} row={row} />)
      )}
    </Field>
  );
}
