import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import RunStream from "./RunStream";
import { initial } from "../api/poll";
import { NO_RUNS, type LiveGraph } from "../api/useLiveGraph";
import { cliRunRow, logReport, runRow } from "../api/wire.fixture";
import type { RunRow, RunsView, Status } from "../api/types";

const RUN = runRow({ trigger_kind: "watch" });

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

function ready(rows: RunRow[], over: Partial<RunsView["log"]> = {}): LiveGraph["runs"] {
  return {
    phase: "ready",
    data: { log: logReport({ runs: rows, ...over }) },
    error: "",
    attempts: 0,
  };
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
    const second = runRow({ run_id: "other", trigger_kind: "manual" });
    render(<RunStream live={live(ready([RUN, second]))} />);
    const [first, other] = screen.getAllByRole("row").slice(1);
    fireEvent.click(first);
    expect(first.style.background).not.toBe("");
    expect(other.style.background).toBe("");
  });

  it("a cli run renders, though it has no session, no branch and no commit to show", () => {
    render(<RunStream live={live(ready([cliRunRow()]))} />);
    const row = screen.getByRole("row", { name: /manual/ });
    expect(row.textContent).toContain("-@-");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a skipped reason collapses into one chip carrying its count, not a stack of blocks", () => {
    const skipped = runRow({
      run_id: "s1",
      status: "skipped",
      trigger_detail: { assessment: { verdict: { decision: "skip", reason: "no new facts" } } },
    });
    render(<RunStream live={live(ready([RUN, skipped]))} />);
    const chip = screen.getByRole("button", { name: /skipped: no new facts/ });
    expect(chip.textContent).toBe("1 skipped: no new facts");
  });
});
