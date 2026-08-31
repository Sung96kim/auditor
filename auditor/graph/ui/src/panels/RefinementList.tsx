import { useEffect, useState } from "react";
import { getJson } from "../api/client";
import { failed, initial, received, type PollState } from "../api/poll";
import type { RefinementRow } from "../api/types";
import { THEME } from "../theme";
import { Empty, Failed, Loading } from "./States";

interface RefinementsBody {
  refinements: { rows: RefinementRow[]; refinement_count: number; truncated: boolean };
}

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

/** Named so a status with no rows still shows its heading and a zero rather than vanishing. */
const NAMED = ["pending", "active", "pinned", "redundant", "rejected", "reverted"];

/** Spec 12.1's C14. Fetched on panel open, never on the 3 s cycle (P3). */
export default function RefinementList({ base, repo }: { base: string; repo: string }) {
  const [state, setState] = useState<PollState<RefinementsBody>>(() =>
    initial<RefinementsBody>(),
  );

  useEffect(() => {
    let alive = true;
    const query = new URLSearchParams({ repo });
    getJson<RefinementsBody>(`${base}api/refinements?${query}`, "")
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
  const groups = new Map<string, RefinementRow[]>(NAMED.map((name) => [name, []]));
  for (const row of rows) {
    const bucket = groups.get(row.status) ?? [];
    bucket.push(row);
    groups.set(row.status, bucket);
  }

  return (
    <div style={card} data-testid="RefinementList">
      <span style={header}>Refinements</span>
      {state.phase === "loading" ? <Loading what="refinements" /> : null}
      {state.phase === "error" ? (
        <Failed error={state.error} onRetry={() => setState(initial<RefinementsBody>())} />
      ) : null}

      {state.phase === "ready" && rows.length === 0 ? (
        <Empty what="refinements" hint="no refinement has been proposed for this repo yet" />
      ) : null}

      {rows.length > 0
        ? [...groups].map(([status, group]) => (
            <div key={status} style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
              <span style={header}>
                {status} ({group.length})
              </span>
              {group.map((row) => (
                <span
                  key={row.refinement_id}
                  style={{ fontFamily: "monospace", fontSize: "11px", color: "#94a3b8" }}
                >
                  [{row.tier}] {row.kind} {row.src ?? row.node_id ?? "-"}
                  {row.drifted ? " (drifted)" : ""}
                </span>
              ))}
            </div>
          ))
        : null}
    </div>
  );
}
