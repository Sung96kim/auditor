import { describe, it, expect } from "vitest";
import {
  REFINEMENT_STATUSES,
  STATUS_GROUPS,
  accepted,
  costLabel,
  duration,
  otherStatuses,
  rejected,
  skipReason,
  stream,
  runTone,
  RUN_TONES,
} from "./runs";
import { runRow } from "../api/wire.fixture";
import type { RunRow } from "../api/types";

function row(over: Partial<RunRow> = {}): RunRow {
  return runRow({ summary: "", error: "", cost_usd: 0.0123, started_at: 100, finished_at: 130, ...over });
}

describe("the run stream's derived columns", () => {
  it("duration is finished minus started, because no field carries it", () => {
    expect(duration(row())).toBe(30);
  });

  it("an open run has no duration rather than a negative one", () => {
    expect(duration(row({ finished_at: 0 }))).toBeNull();
    expect(duration(row({ finished_at: 50 }))).toBeNull();
  });

  it("an estimated cost carries the tilde spec 12.1 asks for", () => {
    expect(costLabel(row({ cost_estimated: true }))).toBe("~$0.0123");
    expect(costLabel(row())).toBe("$0.0123");
  });

  it("a run that cost nothing says so in words, never with a dash", () => {
    expect(costLabel(row({ cost_usd: 0 }))).toBe("no cost");
  });

  it("an estimated zero keeps its tilde rather than reading as a settled free run", () => {
    expect(costLabel(row({ cost_usd: 0, cost_estimated: true }))).toBe("~$0.0000");
  });
});

describe("collapsing skipped rows", () => {
  it("skipped rows leave the stream and are counted by their reason", () => {
    const rows = [
      row({ run_id: "a" }),
      row({ run_id: "b", status: "skipped", trigger_detail: { assessment: { verdict: { decision: "skip", reason: "trivial" } } } }),
      row({ run_id: "c", status: "skipped", trigger_detail: { assessment: { verdict: { decision: "skip", reason: "trivial" } } } }),
      row({ run_id: "d", status: "skipped", summary: "cooldown" }),
    ];
    const out = stream(rows);
    expect(out.shown.map((r) => r.run_id)).toEqual(["a"]);
    expect(out.collapsed).toHaveLength(3);
    expect(out.reasons.get("trivial")).toBe(2);
    expect(out.reasons.get("cooldown")).toBe(1);
  });

  it("a skipped row with no reason at all still groups under a stable label", () => {
    expect(skipReason(row({ status: "skipped" }))).toBe("no reason recorded");
  });

  it("an empty ledger collapses to an empty stream, not to undefined", () => {
    const out = stream([]);
    expect(out.shown).toEqual([]);
    expect(out.collapsed).toEqual([]);
    expect(out.reasons.size).toBe(0);
  });
});

describe("run detail's accepted and rejected split", () => {
  it("one status-discriminated list becomes the two lists spec 12.1 asks for", () => {
    const rows = [
      { status: "active" },
      { status: "pinned" },
      { status: "rejected" },
      { status: "reverted" },
      { status: "pending" },
    ];
    expect(accepted(rows)).toEqual([{ status: "active" }, { status: "pinned" }]);
    expect(rejected(rows)).toEqual([{ status: "rejected" }, { status: "reverted" }]);
  });

  it("no refinement is ever `accepted` on the wire, so the filter reads the enum's own words", () => {
    expect(accepted([{ status: "accepted" }])).toEqual([]);
  });

  it("every status the map holds lands in exactly one of the three lists", () => {
    // the list itself is held to the wire by `test_ui_contract.py`, which reads the schema
    expect(REFINEMENT_STATUSES).toEqual(Object.keys(STATUS_GROUPS));
    expect(REFINEMENT_STATUSES).toHaveLength(8);
    const rows = REFINEMENT_STATUSES.map((status) => ({ status }));
    expect(
      accepted(rows).length + rejected(rows).length + otherStatuses(rows).length,
    ).toBe(REFINEMENT_STATUSES.length);
  });

  it("a status the map has never seen is `other`, so a new member shows rather than disappears", () => {
    expect(otherStatuses([{ status: "invented" }])).toEqual([{ status: "invented" }]);
  });
});

describe("the status column's tone", () => {
  it.each([
    ["queued", "idle"],
    ["running", "busy"],
    ["succeeded", "ok"],
    ["failed", "bad"],
    ["aborted", "bad"],
    ["rejected", "warn"],
    ["skipped", "idle"],
  ])("%s reads as %s", (status, tone) => {
    expect(runTone(status)).toBe(tone);
  });

  it("every RunStatus the wire serves is mapped, so none falls through to grey", () => {
    expect(Object.keys(RUN_TONES).sort()).toEqual([
      "aborted",
      "failed",
      "queued",
      "rejected",
      "running",
      "skipped",
      "succeeded",
    ]);
  });

  it("a finished run, a dead one and a turned-down one are three different colours", () => {
    expect(new Set(["succeeded", "failed", "rejected"].map(runTone)).size).toBe(3);
  });
});
