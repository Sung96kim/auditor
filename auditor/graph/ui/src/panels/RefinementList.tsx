import { useEffect, useState } from "react";
import { getJson } from "../api/client";
import { failed, initial, received, type PollState } from "../api/poll";
import type { RefinementRow } from "../api/types";
import { TEXT } from "../theme";
import Panel, { block, microLabel } from "./Panel";
import { Empty, Failed, Loading } from "./States";

interface RefinementsBody {
  refinements: { rows: RefinementRow[]; refinement_count: number; truncated: boolean };
}

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
    <Panel title="Refinements" testId="RefinementList">
      {state.phase === "loading" ? <Loading what="refinements" /> : null}
      {state.phase === "error" ? (
        <Failed error={state.error} onRetry={() => setState(initial<RefinementsBody>())} />
      ) : null}

      {state.phase === "ready" && rows.length === 0 ? (
        <Empty what="refinements" hint="no refinement has been proposed for this repo yet" />
      ) : null}

      {rows.length > 0
        ? [...groups].map(([status, group]) => (
            <div key={status} style={block}>
              <span style={microLabel}>
                {status} ({group.length})
              </span>
              {group.map((row) => (
                <span
                  key={row.refinement_id}
                  style={{ fontFamily: "monospace", fontSize: "11px", color: TEXT.body }}
                >
                  [{row.tier}] {row.kind} {row.src ?? row.node_id ?? "-"}
                  {row.drifted ? " (drifted)" : ""}
                </span>
              ))}
            </div>
          ))
        : null}
    </Panel>
  );
}
