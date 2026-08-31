import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import Chrome from "./Chrome";
import { initial, received } from "../api/poll";
import { repo as aRepo, runnerEval, status as aStatus } from "../api/wire.fixture";
import type { Status } from "../api/types";

const REPO = aRepo({ repo: "/w/auditor" });
const STATUS = aStatus({
  repos: [REPO],
  evals: [runnerEval({ model: "a-very-long-model-name-indeed" })],
});

function draw(status: Status, repo = REPO.repo) {
  return render(
    <Chrome
      status={received(initial<Status>(), status)}
      repo={repo}
      onChooseRepo={vi.fn()}
      onRetry={vi.fn()}
    />,
  );
}

afterEach(cleanup);

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

  it("a long model name truncates on one line rather than wrapping the eval row", () => {
    draw(STATUS);
    const model = screen.getByTitle("a-very-long-model-name-indeed");
    expect(model.style.whiteSpace).toBe("nowrap");
    expect(model.style.textOverflow).toBe("ellipsis");
  });
});
