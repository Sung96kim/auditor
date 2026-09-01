import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import RunStream from "./RunStream";
import { initial } from "../api/poll";
import { NO_RUNS, type LiveGraph } from "../api/useLiveGraph";
import { cliRunRow, logReport, runDetail, runRow } from "../api/wire.fixture";
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
    at: Date.now(),
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

  it("opening a second run never shows the first one's contents under the new id", async () => {
    const detail = (prompt: string) =>
      new Response(JSON.stringify({ ...runDetail(), prompt }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(detail(call++ === 0 ? "the first brief" : "the second brief"))),
    );
    const second = runRow({ run_id: "0000second00000", trigger_kind: "manual" });
    render(<RunStream live={live(ready([RUN, second]))} />);
    const [first, other] = screen.getAllByRole("row").slice(1);
    fireEvent.click(first);
    expect(await screen.findByText("the first brief")).not.toBeNull();
    fireEvent.click(other);
    expect(screen.queryByText("the first brief")).toBeNull();
    expect(await screen.findByText("the second brief")).not.toBeNull();
  });

  it("a clipped session id says it is clipped and carries the whole of it", () => {
    render(
      <RunStream live={live(ready([runRow({ session_id: "vr-session-8812" })]))} />,
    );
    const cell = screen.getByTitle("vr-session-8812");
    expect(cell.textContent).toBe("vr-sessi\u2026");
  });

  it("a commit sha keeps its conventional seven, and the whole sha is still on the page", () => {
    render(
      <RunStream live={live(ready([runRow({ commit_sha: "7c7f6dbfa1129e" })]))} />,
    );
    const cell = screen.getByTitle("7c7f6dbfa1129e");
    expect(cell.textContent).toBe("7c7f6db");
  });

  it("a run that is queued is not timed as running, whatever the duration column has", () => {
    render(
      <RunStream
        live={live(ready([runRow({ status: "queued", finished_at: null })]))}
      />,
    );
    const row = screen.getByRole("row", { name: /queued/ });
    expect(row.textContent).not.toContain("running");
  });

  it("a cli run renders, though it has no session, no branch and no commit to show", () => {
    render(<RunStream live={live(ready([cliRunRow()]))} />);
    const row = screen.getByRole("row", { name: /manual/ });
    expect(row.textContent).toContain("-@-");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("the withheld rows are offered by the count the server sent, not by rows it did not", () => {
    render(<RunStream live={live(ready([RUN], { hidden_count: 4 }))} />);
    const show = screen.getByRole("button", { name: /skipped, show them/ });
    expect(show.textContent).toBe("4 skipped, show them");
  });

  it("asking for the withheld rows is what puts skipped=1 on the wire", () => {
    const graph = live(ready([RUN], { hidden_count: 4 }));
    render(<RunStream live={graph} />);
    fireEvent.click(screen.getByRole("button", { name: /skipped, show them/ }));
    expect(graph.setShowSkipped).toHaveBeenCalledWith(true);
  });

  it("the rows the server then sends are drawn, and the control turns back into a way out", () => {
    const skipped = runRow({
      run_id: "s1",
      status: "skipped",
      trigger_detail: { assessment: { verdict: { decision: "skip", reason: "no new facts" } } },
    });
    const graph = { ...live(ready([RUN, skipped])), showSkipped: true };
    render(<RunStream live={graph} />);
    expect(screen.getAllByRole("row")).toHaveLength(3);
    const hide = screen.getByRole("button", { name: /skipped, hide them/ });
    expect(hide.textContent).toBe("1 skipped, hide them");
    fireEvent.click(hide);
    expect(graph.setShowSkipped).toHaveBeenCalledWith(false);
  });

  it("a skipped reason is summarised in one chip carrying its count, not a stack of blocks", () => {
    const skipped = runRow({
      run_id: "s1",
      status: "skipped",
      trigger_detail: { assessment: { verdict: { decision: "skip", reason: "no new facts" } } },
    });
    render(<RunStream live={{ ...live(ready([RUN, skipped])), showSkipped: true }} />);
    expect(screen.getByText(/skipped: no new facts/).textContent).toBe(
      "1 skipped: no new facts",
    );
  });

  it("a truncated page says how many rows it is not showing", () => {
    render(<RunStream live={live(ready([RUN], { run_count: 91, truncated: true }))} />);
    const strip = screen.getByTestId("RunStream").firstElementChild;
    expect(strip?.textContent).toBe("Runs1 of 91");
  });
});
