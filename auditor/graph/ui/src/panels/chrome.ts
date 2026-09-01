import type { Bootstrap } from "../api/bootstrap";
import type { Budget, RateLimit, Repo, RunnerEval, VectorStatus } from "../api/types";

export interface Meter {
  /** 0 to 1, already clamped, so a bar can multiply by its width without checking. */
  fill: number;
  label: string;
  tone: "ok" | "low" | "spent";
  known: boolean;
}

/** The budget meter. A repo whose loop has not published yet has no budget, and says so.
 *
 * The bar and the caption read the same ceiling. `remaining_fraction` is measured against runs
 * for an unpriced model and against dollars for a priced one, so a caption that always spoke
 * dollars sat frozen at `$0.00` while the bar filled up.
 */
export function budgetMeter(budget: Budget | null): Meter {
  if (budget === null) {
    return { fill: 0, label: "no budget yet", tone: "ok", known: false };
  }
  const fill = Math.min(1, Math.max(0, 1 - budget.remaining_fraction));
  const used = budget.priced
    ? `$${budget.spent_usd.toFixed(2)} of $${budget.max_cost_usd_per_day.toFixed(2)}`
    : `${budget.runs} of ${budget.max_runs_per_day} runs today`;
  if (budget.exhausted) return { fill: 1, label: `${used}, spent`, tone: "spent", known: true };
  return { fill, label: used, tone: budget.low ? "low" : "ok", known: true };
}

/** The rate-limit meter, whose only interesting state is a pause with a time on it.
 *
 * Null is the repo the roster does not hold, a stale bookmark or a repo dropped from the shared
 * index; it reads the way the budget's own unknown does rather than removing the bar.
 */
export function limitMeter(limits: RateLimit | null, now: number): Meter {
  if (limits === null) {
    return { fill: 0, label: "no rate limit yet", tone: "ok", known: false };
  }
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

/** The badge's colour, so a paused or detached loop is not the same green as a working one. */
export type StateTone = "ok" | "busy" | "warn" | "bad" | "idle";

/** `LoopState` as a tone. Auth and error pauses are failures; budget and rate limit are waits. */
export function stateTone(state: string): StateTone {
  if (state === "paused:auth" || state === "paused:error") return "bad";
  if (state.startsWith("paused")) return "warn";
  if (state === "running" || state === "building") return "busy";
  if (state === "observing") return "ok";
  return "idle";
}

export type PanelMode = "static" | "no repo" | "live";

/** Which of the three reachable pages this bundle is on, decided before anything renders. */
export function panelMode(boot: Bootstrap): PanelMode {
  if (!boot.live) return "static";
  return boot.repo ? "live" : "no repo";
}

export interface StratumLine {
  key: string;
  label: string;
  /** the 95% lower bound, which is the number the tier gate reads, not the point estimate. */
  lower: number;
  proven: boolean;
}

export interface EvalLine {
  runner: string;
  /** the runner's own pinned model, or the words for a runner that has none configured. */
  model: string;
  /** false until a suite has been run against this runner: the block says so rather than hiding. */
  measured: boolean;
  proven: number;
  strata: StratumLine[];
}

/** Spec 12.1's "latest eval per runner with lower bounds per stratum", empty state included.
 *
 * Two sources by design: `/api/status` carries the roster, which is which runners exist and what
 * each is pinned to, and `/api/evals` carries the measurements. The roster decides the rows, so
 * the block lays out before the second fetch lands and a runner with no eval still gets a line.
 */
export function evalLines(roster: RunnerEval[], measured: RunnerEval[] = []): EvalLine[] {
  const numbers = new Map(measured.map((row) => [row.runner, row]));
  return roster.map((row) => {
    const found = numbers.get(row.runner) ?? row;
    return {
      runner: row.runner,
      model: row.model || "no model configured",
      measured: found.measured > 0,
      proven: found.proven,
      strata: found.strata.map((s) => ({
        key: `${s.suite}/${s.stratum}`,
        label: s.stratum,
        lower: s.lower_bound_95,
        proven: s.proven,
      })),
    };
  });
}

/** A clock reading the payload took, carried forward to now: a 304 must not freeze it (H2). */
export function sinceLabel(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}s`;
  if (whole < 3600) return `${Math.floor(whole / 60)}m`;
  if (whole < 86400) return `${Math.floor(whole / 3600)}h`;
  return `${Math.floor(whole / 86400)}d`;
}

/** The repo the URL names, out of the roster the daemon serves, or null when it holds no such row. */
export function selectedRepo(repos: Repo[], repo: string): Repo | null {
  return repos.find((row) => row.repo === repo) ?? null;
}

/** The vector layer is S13's; until then the block reports off rather than being absent. */
export function vectorLabel(vectors: VectorStatus): string {
  if (!vectors.enabled) return "vectors off";
  return vectors.ready ? `vectors ready (${vectors.model})` : "vectors warming up";
}

/** The switcher's label. `graph.meta` carries no repo, so the name comes from `/api/status`. */
export function repoLabel(repo: Repo): string {
  const tail = repo.repo.split("/").filter(Boolean).pop();
  return tail || repo.repo;
}
