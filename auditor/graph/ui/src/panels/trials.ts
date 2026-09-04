import type { TuningRow } from "../api/types";

/** One trial's line: what it proposed, what it did to the clustering, and the guard's verdict
 * (spec 12.1). A refused trial has numbers too, so the verdict replaces them rather than sitting
 * beside them. */
export function trialLine(trial: TuningRow): string {
  const m = trial.metrics;
  const head = `${trial.value} (${trial.status})`;
  if (!m.measured_at) return `${head}: not measured yet`;
  if (m.refused) return `${head}: refused, ${m.refused}`;
  const pct = (n: number) => `${(n * 100).toFixed(1)}%`;
  return (
    `${head}: clusters ${m.baseline.clusters} to ${m.clusters}, ` +
    `name-edge churn ${pct(m.name_edge_churn)}, label churn ${pct(m.label_churn)}`
  );
}
