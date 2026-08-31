import type { RunRow } from "../api/types";

/** Wall time in seconds, or null while the run is still open. Derived: no field carries it. */
export function duration(row: RunRow): number | null {
  if (!row.finished_at || row.finished_at < row.started_at) return null;
  return row.finished_at - row.started_at;
}

/** Spec 12.1's cost column, with the `~` an estimated price earns, a priced zero included. */
export function costLabel(row: RunRow): string {
  if (row.cost_usd === 0 && !row.cost_estimated) return "no cost";
  const money = `$${row.cost_usd.toFixed(4)}`;
  return row.cost_estimated ? `~${money}` : money;
}

/** The status column's colour. Every `RunStatus` the wire serves is mapped, so none reads grey
 * by accident: a run that died and a run the gate turned down are not the same news. */
export type RunTone = "ok" | "busy" | "warn" | "bad" | "idle";

export const RUN_TONES: Record<string, RunTone> = {
  queued: "idle",
  running: "busy",
  succeeded: "ok",
  failed: "bad",
  aborted: "bad",
  rejected: "warn",
  skipped: "idle",
};

export function runTone(status: string): RunTone {
  return RUN_TONES[status] ?? "idle";
}

/** The duration column: wall time, or the word for a run that has not stopped yet. */
export function durationLabel(row: RunRow): string {
  const seconds = duration(row);
  return seconds === null ? "running" : `${seconds.toFixed(1)}s`;
}

export interface Stream {
  shown: RunRow[];
  /** every skipped row the server sent, whether or not the reader asked to see them drawn. */
  collapsed: RunRow[];
  reasons: Map<string, number>;
}

/** Spec 12.1: `skipped` rows collapse behind their reason rather than filling the stream.
 *
 * `showSkipped` is an input because the disclosure is server-driven: asking for them puts
 * `skipped=1` on the wire, and re-hiding them here is what makes the control reversible.
 */
export function stream(rows: RunRow[], showSkipped = false): Stream {
  const shown: RunRow[] = [];
  const collapsed: RunRow[] = [];
  const reasons = new Map<string, number>();
  for (const row of rows) {
    if (row.status !== "skipped") {
      shown.push(row);
      continue;
    }
    collapsed.push(row);
    const reason = skipReason(row);
    reasons.set(reason, (reasons.get(reason) ?? 0) + 1);
    if (showSkipped) shown.push(row);
  }
  return { shown, collapsed, reasons };
}

/** The assessment's own word for why it declined, or a stable stand-in when it left none.
 *
 * The reason lives on the verdict: `AssessmentPayload` has no `reason` of its own, so reading one
 * off it was always undefined and every collapsed group fell back to the summary.
 */
export function skipReason(row: RunRow): string {
  const assessment = row.trigger_detail["assessment"] as
    | { verdict?: { reason?: string } | null }
    | undefined;
  return assessment?.verdict?.reason || row.summary || "no reason recorded";
}

export type StatusGroup = "accepted" | "rejected" | "other";

/** Every `RefinementStatus` the wire serves, mapped once, so none can vanish from run detail. */
export const STATUS_GROUPS: Record<string, StatusGroup> = {
  pending: "other",
  active: "accepted",
  stale: "other",
  redundant: "other",
  reverted: "rejected",
  pinned: "accepted",
  superseded: "other",
  rejected: "rejected",
};

/** The one list of every `RefinementStatus`, so no panel can name a different set (M8). */
export const REFINEMENT_STATUSES = Object.keys(STATUS_GROUPS);

function grouped<T extends { status: string }>(rows: T[], group: StatusGroup): T[] {
  return rows.filter((r) => (STATUS_GROUPS[r.status] ?? "other") === group);
}

/** Spec 12.1's "accepted changes": `accept()` writes `active`, and a pinned row stays accepted. */
export function accepted<T extends { status: string }>(rows: T[]): T[] {
  return grouped(rows, "accepted");
}

export function rejected<T extends { status: string }>(rows: T[]): T[] {
  return grouped(rows, "rejected");
}

/** Neither list's business, and four of the eight statuses land here rather than disappearing. */
export function otherStatuses<T extends { status: string }>(rows: T[]): T[] {
  return grouped(rows, "other");
}
