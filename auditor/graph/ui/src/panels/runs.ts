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

export interface Stream {
  shown: RunRow[];
  collapsed: RunRow[];
  reasons: Map<string, number>;
}

/** Spec 12.1: `skipped` rows collapse behind their reason rather than filling the stream. */
export function stream(rows: RunRow[]): Stream {
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
  }
  return { shown, collapsed, reasons };
}

/** The assessment's own word for why it declined, or a stable stand-in when it left none. */
export function skipReason(row: RunRow): string {
  const detail = row.trigger_detail ?? {};
  const assessment = detail["assessment"] as { reason?: string } | undefined;
  return assessment?.reason || row.summary || "no reason recorded";
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
