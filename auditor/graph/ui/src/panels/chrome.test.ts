import { describe, it, expect } from "vitest";
import {
  budgetMeter,
  evalLines,
  limitMeter,
  panelMode,
  repoLabel,
  repoState,
  selectedRepo,
  sinceLabel,
  stateTone,
  vectorLabel,
} from "./chrome";
import { repo as repoFixture, runnerEval, stratum } from "../api/wire.fixture";
import type { Budget, Repo } from "../api/types";

const BUDGET: Budget = {
  spent_usd: 0.5,
  runs: 3,
  max_cost_usd_per_day: 2,
  max_runs_per_day: 40,
  remaining_fraction: 0.75,
  low: false,
  exhausted: false,
};

const REPO: Repo = {
  repo: "/home/u/work/auditor",
  identity: "i",
  repo_dir_key: "k",
  attached: true,
  sessions: 1,
  queued: false,
  state: "observing",
  budget: BUDGET,
  limits: { max_utilization: 0.2, paused: false, resumes_at: null },
};

describe("the budget meter", () => {
  it("fills by what was spent, not by what is left", () => {
    expect(budgetMeter(BUDGET).fill).toBeCloseTo(0.25);
    expect(budgetMeter(BUDGET).label).toBe("$0.50 of $2.00");
  });

  it("a repo whose loop has not published yet says so rather than drawing an empty bar", () => {
    const meter = budgetMeter(null);
    expect(meter.known).toBe(false);
    expect(meter.label).toBe("no budget yet");
  });

  it("an exhausted budget is full and toned apart from a merely low one", () => {
    expect(budgetMeter({ ...BUDGET, exhausted: true, remaining_fraction: 0 }).tone).toBe("spent");
    expect(budgetMeter({ ...BUDGET, low: true }).tone).toBe("low");
  });
});

describe("the rate-limit meter", () => {
  it("draws the window's utilization while nothing is paused", () => {
    expect(limitMeter({ max_utilization: 0.42, paused: false, resumes_at: null }, 0).label).toBe(
      "42% of the window",
    );
  });

  it("a pause with a deadline counts down rather than saying nothing", () => {
    expect(limitMeter({ max_utilization: 1, paused: true, resumes_at: 130 }, 100).label).toBe(
      "paused, 30s left",
    );
  });

  it("a pause with no deadline still reads as paused", () => {
    expect(limitMeter({ max_utilization: 1, paused: true, resumes_at: null }, 100).label).toBe(
      "paused",
    );
  });

  it("a deadline that has arrived is a countdown at zero, never a missing one", () => {
    expect(limitMeter({ max_utilization: 1, paused: true, resumes_at: 0 }, 0).label).toBe(
      "paused, 0s left",
    );
  });
});

describe("the state badge and the switcher", () => {
  it("reads the selected repo's own loop state", () => {
    expect(repoState(REPO)).toBe("observing");
  });

  it("a repo with no loop built yet reads as not started, never as empty", () => {
    expect(repoState({ ...REPO, state: "" })).toBe("not started");
  });

  it("no repo selected is its own word, because the daemon's page starts there", () => {
    expect(repoState(null)).toBe("no repo");
  });

  it("the switcher labels a repo by its directory, since the graph meta carries no repo", () => {
    expect(repoLabel(REPO)).toBe("auditor");
    expect(repoLabel({ ...REPO, repo: "/" })).toBe("/");
  });
});

describe("which of the three pages this bundle is on", () => {
  it("no bootstrap is static, which is `graph serve` and a bare `pnpm dev`", () => {
    expect(panelMode({ live: false, base: "", repo: "" })).toBe("static");
  });

  it("the daemon's own open_browser URL names no repo and is its own mode", () => {
    expect(panelMode({ live: true, base: "/", repo: "" })).toBe("no repo");
  });

  it("a bootstrap naming a repo is the full live page", () => {
    expect(panelMode({ live: true, base: "/", repo: "/w" })).toBe("live");
  });
});

describe("latest eval per runner", () => {
  const MEASURED = runnerEval({
    measured: 2,
    proven: 1,
    strata: [
      stratum({ suite: "edges", stratum: "calls", lower_bound_95: 0.81, proven: true }),
      stratum({ suite: "edges", stratum: "imports", lower_bound_95: 0.64, proven: false }),
    ],
  });

  it("a runner with no eval is a line saying so, not a missing row", () => {
    const lines = evalLines([runnerEval({ runner: "codex", model: "gpt-5" })]);
    expect(lines).toHaveLength(1);
    expect(lines[0].measured).toBe(false);
    expect(lines[0].strata).toEqual([]);
  });

  it("a runner with no model of its own says so, never another runner's model", () => {
    const lines = evalLines([runnerEval({ runner: "codex", model: "" })]);
    expect(lines[0].model).toBe("no model configured");
  });

  it("the roster decides the rows and the measurements route fills the numbers", () => {
    const lines = evalLines([runnerEval(), runnerEval({ runner: "codex", model: "gpt-5" })], [
      MEASURED,
    ]);
    expect(lines.map((l) => l.runner)).toEqual(["claude", "codex"]);
    expect(lines[0].measured).toBe(true);
    expect(lines[0].proven).toBe(1);
    expect(lines[1].measured).toBe(false);
  });

  it("every stratum carries its own lower bound, which is what the tier gate reads", () => {
    const [line] = evalLines([runnerEval()], [MEASURED]);
    expect(line.strata.map((s) => s.label)).toEqual(["calls", "imports"]);
    expect(line.strata.map((s) => s.lower)).toEqual([0.81, 0.64]);
    expect(line.strata.map((s) => s.proven)).toEqual([true, false]);
    expect(line.strata.map((s) => s.key)).toEqual(["edges/calls", "edges/imports"]);
  });

  it("the roster's own model stands even when the measured row was pinned to another", () => {
    const [line] = evalLines(
      [runnerEval({ model: "claude-sonnet-4-5" })],
      [runnerEval({ model: "an-older-pin", measured: 3 })],
    );
    expect(line.model).toBe("claude-sonnet-4-5");
    expect(line.measured).toBe(true);
  });
});

describe("a clock reading the page carries forward", () => {
  it.each([
    [0, "0s"],
    [45, "45s"],
    [90, "1m"],
    [7200, "2h"],
    [200000, "2d"],
  ])("%s seconds reads as %s", (seconds, words) => {
    expect(sinceLabel(seconds)).toBe(words);
  });

  it("a negative reading is zero rather than a minus sign", () => {
    expect(sinceLabel(-4)).toBe("0s");
  });
});

describe("the repo the URL names", () => {
  it("is the roster row with that path", () => {
    const rows = [repoFixture({ repo: "/a" }), repoFixture({ repo: "/b" })];
    expect(selectedRepo(rows, "/b")?.repo).toBe("/b");
  });

  it("is null for a repo the daemon's roster does not hold, never the first row", () => {
    expect(selectedRepo([repoFixture({ repo: "/a" })], "/gone")).toBeNull();
    expect(selectedRepo([], "/a")).toBeNull();
  });
});

describe("the vector layer status", () => {
  it("reports off rather than vanishing, because S13 owns the field", () => {
    expect(vectorLabel({ enabled: false, model: "", ready: false })).toBe("vectors off");
  });

  it("distinguishes enabled from ready", () => {
    expect(vectorLabel({ enabled: true, model: "m2v", ready: false })).toBe("vectors warming up");
    expect(vectorLabel({ enabled: true, model: "m2v", ready: true })).toBe("vectors ready (m2v)");
  });
});

describe("the badge's tone", () => {
  it.each([
    ["building", "busy"],
    ["observing", "ok"],
    ["running", "busy"],
    ["paused:budget", "warn"],
    ["paused:ratelimit", "warn"],
    ["paused:auth", "bad"],
    ["paused:error", "bad"],
    ["detached", "idle"],
  ])("%s reads as %s", (state, tone) => {
    expect(stateTone(state)).toBe(tone);
  });

  it("a repo with no loop yet is idle, not a failure and not a working state", () => {
    expect(stateTone(repoState(null))).toBe("idle");
    expect(stateTone(repoState({ ...REPO, state: "" }))).toBe("idle");
  });

  it("a wait and a failure are told apart, so every pause is not one colour", () => {
    expect(stateTone("paused:budget")).not.toBe(stateTone("paused:auth"));
  });
});
