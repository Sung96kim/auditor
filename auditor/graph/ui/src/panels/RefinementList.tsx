import { useEffect, useState } from "react";
import { getJson } from "../api/client";
import { failed, initial, received, type PollState } from "../api/poll";
import type { RefinementRow, RefinementsView } from "../api/types";
import { TEXT, THEME, TONE } from "../theme";
import { REFINEMENT_STATUSES } from "./runs";
import Panel, { block, microLabel, mono } from "./Panel";
import { Empty, Failed, Loading } from "./States";

const row: React.CSSProperties = { ...mono, overflowWrap: "anywhere" };

/** Spec 12.1's C14. Fetched on panel open, never on the 3 s cycle (P3). */
export default function RefinementList({ base, repo }: { base: string; repo: string }) {
  const [state, setState] = useState<PollState<RefinementsView>>(() =>
    initial<RefinementsView>(),
  );

  useEffect(() => {
    let alive = true;
    const query = new URLSearchParams({ repo });
    getJson<RefinementsView>(`${base}api/refinements?${query}`, "")
      .then((got) => {
        if (alive) setState((prev) => received(prev, got.value));
      })
      .catch((err) => {
        if (alive) setState((prev) => failed(prev, String(err)));
      });
    return () => {
      alive = false;
    };
  }, [base, repo]);

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
      {state.phase === "loading" ? <Loading what="refinements" /> : null}
      {state.phase === "error" ? (
        <Failed error={state.error} onRetry={() => setState(initial<RefinementsView>())} />
      ) : null}

      {state.phase === "ready" && rows.length === 0 ? (
        <Empty what="refinements" hint="no refinement has been proposed for this repo yet" />
      ) : null}

      {filled.map(([status, group]) => (
        <div key={status} style={block}>
          <span style={microLabel}>
            {status} ({group.length})
          </span>
          {group.map((held) => (
            <span key={held.refinement_id} style={row}>
              <span style={{ color: TEXT.label }}>[{held.tier}]</span> {held.kind}{" "}
              {held.src ?? held.node_id ?? "-"}
              {held.drifted ? <span style={{ color: TONE.warn }}> drifted</span> : null}
            </span>
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
