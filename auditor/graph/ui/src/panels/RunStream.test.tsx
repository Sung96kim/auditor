import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import RunStream from "./RunStream";
import { initial } from "../api/poll";
import { NO_RUNS, type LiveGraph, type RunsBody } from "../api/useLiveGraph";
import type { RunRow, Status } from "../api/types";

const RUN: RunRow = {
  run_id: "3f2a1b9c44de4c7f",
  status: "succeeded",
  producer: "observer",
  client: "cli",
  runner: "claude",
  trigger_kind: "watch",
  trigger_detail: null,
  model: "claude-sonnet-4-5",
  summary: null,
  error: null,
  session_id: "b71ce0f2aa11",
  branch: "main",
  commit_sha: "309bb81ac4419f",
  cost_usd: 0.04,
  cost_estimated: false,
  started_at: 1000,
  finished_at: 1042,
};

function live(runs: LiveGraph["runs"]): LiveGraph {
  return {
    boot: { live: true, base: "/", repo: "/w" },
    status: initial<Status>(),
    runs,
    showSkipped: false,
    setShowSkipped: vi.fn(),
    chooseRepo: vi.fn(),
    retry: vi.fn(),
  };
}

function ready(rows: RunRow[]): LiveGraph["runs"] {
  const body: RunsBody = {
    log: { runs: rows, hidden_count: 0, run_count: rows.length, truncated: false },
  };
  return { phase: "ready", data: body, error: "", attempts: 0 };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("no daemon in a test"))));
});

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the run stream", () => {
  it("a failed poll is a failure, never a failure plus a claim that the ledger is empty", () => {
    render(<RunStream live={live({ ...initial(NO_RUNS), phase: "error", error: "down" })} />);
    expect(screen.getByRole("alert")).not.toBeNull();
    expect(screen.queryByText("No runs yet")).toBeNull();
  });

  it("an answered poll with nothing in it says the ledger is empty", () => {
    render(<RunStream live={live(ready([]))} />);
    expect(screen.getByText("No runs yet")).not.toBeNull();
  });

  it("a row opens from the keyboard, so the stream is not reachable by mouse alone", () => {
    render(<RunStream live={live(ready([RUN]))} />);
    const row = screen.getByRole("row", { name: /watch/ });
    expect(row.tabIndex).toBe(0);
    fireEvent.keyDown(row, { key: "Enter" });
    expect(screen.getByTestId("RunDetail")).not.toBeNull();
  });

  it("the open row is marked apart from the rest, so the detail below has a source", () => {
    const second = { ...RUN, run_id: "other", trigger_kind: "manual" };
    render(<RunStream live={live(ready([RUN, second]))} />);
    const [first, other] = screen.getAllByRole("row").slice(1);
    fireEvent.click(first);
    expect(first.style.background).not.toBe("");
    expect(other.style.background).toBe("");
  });

  it("a skipped reason collapses into one chip carrying its count, not a stack of blocks", () => {
    const skipped = {
      ...RUN,
      run_id: "s1",
      status: "skipped",
      trigger_detail: { assessment: { reason: "no new facts" } },
    };
    render(<RunStream live={live(ready([RUN, skipped]))} />);
    const chip = screen.getByRole("button", { name: /skipped: no new facts/ });
    expect(chip.textContent).toBe("1 skipped: no new facts");
  });
});
