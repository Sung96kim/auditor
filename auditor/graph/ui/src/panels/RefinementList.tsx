import { answered } from "../api/poll";
import { useFetchOnce } from "../api/useFetchOnce";
import type { RefinementRow, RefinementsView } from "../api/types";
import { TEXT, THEME, TONE } from "../theme";
import { REFINEMENT_STATUSES } from "./runs";
import Panel, { block, microLabel, mono } from "./Panel";
import RefinementLine from "./RefinementLine";
import { Empty, Phases } from "./States";

/** Spec 12.1's C14. Fetched on panel open, never on the 3 s cycle (P3).
 *
 * The reconnect arm stays on because `answered` lets the rows sit on a stale refetch, and a
 * panel that speaks for an answer it cannot refresh has to be able to say the answer went stale.
 * `useFetchOnce` keys on the URL, so a repo that changed in place is exactly that refetch.
 */
export default function RefinementList({ base, repo }: { base: string; repo: string }) {
  const { state, retry } = useFetchOnce<RefinementsView>(
    `${base}api/refinements?${new URLSearchParams({ repo })}`,
  );

  const rows = state.data?.refinements.rows ?? [];
  const total = state.data?.refinements.refinement_count ?? rows.length;
  const truncated = state.data?.refinements.truncated ?? false;
  // every status the wire serves, so one with no rows shows a zero rather than vanishing
  const groups = new Map<string, RefinementRow[]>(
    REFINEMENT_STATUSES.map((name) => [name, []]),
  );
  for (const held of rows) {
    const bucket = groups.get(held.status) ?? [];
    bucket.push(held);
    groups.set(held.status, bucket);
  }
  const filled = [...groups].filter(([, group]) => group.length > 0);
  const empty = [...groups].filter(([, group]) => group.length === 0);

  return (
    <Panel
      title="Refinements"
      testId="RefinementList"
      trailing={
        rows.length > 0 ? (
          <span style={{ ...mono, color: TEXT.label }}>
            {truncated ? `${rows.length} of ${total}` : rows.length}
          </span>
        ) : null
      }
    >
      <Phases state={state} what="refinements" onRetry={retry} />

      {answered(state) && rows.length === 0 ? (
        <Empty what="refinements" hint="no refinement has been proposed for this repo yet" />
      ) : null}

      {filled.map(([status, group]) => (
        <div key={status} style={block}>
          <span style={microLabel}>
            {status} ({group.length})
          </span>
          {group.map((held) => (
            <RefinementLine
              key={held.refinement_id}
              row={held}
              trailing={
                held.drifted ? <span style={{ color: TONE.warn }}> drifted</span> : null
              }
            />
          ))}
        </div>
      ))}

      {rows.length > 0 && empty.length > 0 ? (
        <div
          style={{
            borderTop: `1px solid ${THEME.border}`,
            display: "flex",
            flexWrap: "wrap",
            gap: "5px",
            paddingTop: "9px",
          }}
        >
          {empty.map(([status]) => (
            <span
              key={status}
              style={{
                ...microLabel,
                border: `1px solid ${THEME.border}`,
                borderRadius: "999px",
                padding: "2px 7px",
              }}
            >
              {status} 0
            </span>
          ))}
        </div>
      ) : null}
    </Panel>
  );
}
