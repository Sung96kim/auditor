import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import Chrome from "./Chrome";
import { initial, received } from "../api/poll";
import {
  repo as aRepo,
  runnerEval,
  status as aStatus,
  stratum,
} from "../api/wire.fixture";
import type { RunnerEval, Status } from "../api/types";

const REPO = aRepo({ repo: "/w/auditor" });
const STATUS = aStatus({
  repos: [REPO],
  evals: [runnerEval({ model: "a-very-long-model-name-indeed" })],
});

function serve(runners: RunnerEval[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ runners }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
}

function draw(status: Status, repo = REPO.repo) {
  return render(
    <Chrome
      status={received(initial<Status>(), status)}
      base="/"
      repo={repo}
      onChooseRepo={vi.fn()}
      onRetry={vi.fn()}
    />,
  );
}

beforeEach(() => serve([]));

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the observer card", () => {
  it("the badge carries the repo's own loop state, not the daemon's word", () => {
    draw(STATUS);
    const strip = screen.getByTestId("chrome").firstElementChild;
    expect(strip?.textContent).toBe("Observerobserving");
  });

  it("a repo with no loop yet still gets a badge rather than an empty corner", () => {
    draw(STATUS, "");
    expect(screen.getByTestId("chrome").firstElementChild?.textContent).toContain("no repo");
  });

  it("a repo the roster does not hold reads apart from a page with no repo open", () => {
    draw(STATUS, "/w/repo3");
    const strip = screen.getByTestId("chrome").firstElementChild;
    expect(strip?.textContent).toBe("Observernot tracked");
    expect(strip?.textContent).not.toContain("no repo");
  });

  it("a published budget exposes its reading, so the bar is a value and not a decoration", () => {
    draw(STATUS);
    const bars = screen.getAllByRole("progressbar");
    expect(bars[0].getAttribute("aria-label")).toBe("Budget");
    expect(bars[0].getAttribute("aria-valuenow")).toBe("25");
  });

  it("a budget the loop never published draws no bar at all, because it has no value", () => {
    draw(aStatus({ repos: [aRepo({ repo: "/w/auditor", budget: null })] }));
    expect(screen.getAllByRole("progressbar")).toHaveLength(1);
    expect(screen.getByText("no budget yet")).not.toBeNull();
  });

  it("a repo the roster does not hold reports both meters unknown, never one and not the other", () => {
    draw(STATUS, "/w/gone");
    expect(screen.queryAllByRole("progressbar")).toHaveLength(0);
    expect(screen.getByText("no budget yet")).not.toBeNull();
    expect(screen.getByText("no rate limit yet")).not.toBeNull();
  });

  it("the daemon's own clock is carried forward from when the body was served", () => {
    draw(STATUS);
    const footer = screen.getByText(/running up/);
    expect(footer.textContent).toBe("running up 2m, idle 8s");
  });

  it("the eval block draws each stratum's lower bound, which is the number the gate reads", async () => {
    serve([
      runnerEval({
        measured: 2,
        proven: 1,
        strata: [
          stratum({ suite: "edges", stratum: "calls", lower_bound_95: 0.81, proven: true }),
          stratum({ suite: "edges", stratum: "imports", lower_bound_95: 0.64, proven: false }),
        ],
      }),
    ]);
    draw(STATUS);
    expect(await screen.findByText("0.81")).not.toBeNull();
    expect(screen.getByText("0.64")).not.toBeNull();
    expect(screen.getByText("1 proven")).not.toBeNull();
  });

  it("a runner the measurements route has nothing for still says so in the block", async () => {
    draw(STATUS);
    expect(await screen.findByText("no eval yet")).not.toBeNull();
  });

  it("a long model name truncates on one line rather than wrapping the eval row", () => {
    draw(STATUS);
    const model = screen.getByTitle("a-very-long-model-name-indeed");
    expect(model.style.whiteSpace).toBe("nowrap");
    expect(model.style.textOverflow).toBe("ellipsis");
  });

  it("a failed evals fetch draws the error state, never a false no eval yet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("connection refused"))),
    );
    draw(STATUS);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Could not reach the observer",
    );
    expect(screen.queryByText("no eval yet")).toBeNull();
  });

  it("retry on a failed evals fetch refetches and draws the measurements it gets back", async () => {
    let call = 0;
    const fetcher = vi.fn(() =>
      call++ === 0
        ? Promise.reject(new Error("connection refused"))
        : Promise.resolve(
            new Response(
              JSON.stringify({
                runners: [
                  runnerEval({
                    measured: 1,
                    proven: 1,
                    strata: [stratum({ lower_bound_95: 0.81, proven: true })],
                  }),
                ],
              }),
              { status: 200, headers: { "Content-Type": "application/json" } },
            ),
          ),
    );
    vi.stubGlobal("fetch", fetcher);
    draw(STATUS);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("1 proven")).not.toBeNull();
  });
});
