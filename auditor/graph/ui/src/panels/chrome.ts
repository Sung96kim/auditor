import type { Bootstrap } from "../api/bootstrap";
import type { Budget, RateLimit, Repo, RunnerEval, VectorStatus } from "../api/types";

export interface Meter {
  /** 0 to 1, already clamped, so a bar can multiply by its width without checking. */
  fill: number;
  label: string;
  tone: "ok" | "low" | "spent";
  known: boolean;
}

/** The budget meter. A repo whose loop has not published yet has no budget, and says so. */
export function budgetMeter(budget: Budget | null): Meter {
  if (budget === null) {
    return { fill: 0, label: "no budget yet", tone: "ok", known: false };
  }
  const fill = Math.min(1, Math.max(0, 1 - budget.remaining_fraction));
  const spent = `$${budget.spent_usd.toFixed(2)} of $${budget.max_cost_usd_per_day.toFixed(2)}`;
  if (budget.exhausted) return { fill: 1, label: `${spent}, spent`, tone: "spent", known: true };
  return { fill, label: spent, tone: budget.low ? "low" : "ok", known: true };
}

/** The rate-limit meter, whose only interesting state is a pause with a time on it. */
export function limitMeter(limits: RateLimit, now: number): Meter {
  const fill = Math.min(1, Math.max(0, limits.max_utilization));
  if (!limits.paused) {
    return { fill, label: `${Math.round(fill * 100)}% of the window`, tone: "ok", known: true };
  }
  const left = limits.resumes_at != null ? Math.max(0, Math.round(limits.resumes_at - now)) : null;
  return {
    fill: 1,
    label: left === null ? "paused" : `paused, ${left}s left`,
    tone: "spent",
    known: true,
  };
}

/** Spec 12.1's state badge reads the selected repo's loop state, not the daemon's word. */
export function repoState(repo: Repo | null): string {
  if (repo === null) return "no repo";
  return repo.state || "not started";
}

export type PanelMode = "static" | "no repo" | "live";

/** Which of the three reachable pages this bundle is on, decided before anything renders. */
export function panelMode(boot: Bootstrap): PanelMode {
  if (!boot.live) return "static";
  return boot.repo ? "live" : "no repo";
}

export interface EvalLine {
  runner: string;
  /** the runner's own pinned model, or the words for a runner that has none configured. */
  model: string;
  /** false until a suite has been run against this runner: the block says so rather than hiding. */
  measured: boolean;
  proven: number;
  /** the weakest lower bound across strata, proven or not; `proven` is the gate's own count. */
  floor: number | null;
}

/** Spec 12.1's "latest eval per runner with lower bounds per stratum", empty state included. */
export function evalLines(rows: RunnerEval[]): EvalLine[] {
  return rows.map((row) => ({
    runner: row.runner,
    model: row.model || "no model configured",
    measured: row.measured > 0,
    proven: row.proven,
    floor: row.strata.length ? Math.min(...row.strata.map((s) => s.lower_bound_95)) : null,
  }));
}

/** The vector layer is S13's; until then the block reports off rather than being absent. */
export function vectorLabel(vectors: VectorStatus): string {
  if (!vectors.enabled) return "vectors off";
  return vectors.ready ? `vectors ready (${vectors.model})` : "vectors warming up";
}

/** The switcher's label. `graph.meta` carries no repo, so the name comes from `/api/repos`. */
export function repoLabel(repo: Repo): string {
  const tail = repo.repo.split("/").filter(Boolean).pop();
  return tail || repo.repo;
}
