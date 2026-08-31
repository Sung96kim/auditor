import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import Panels from "./Panels";
import { initial } from "../api/poll";
import { NO_RUNS, type LiveGraph } from "../api/useLiveGraph";
import type { Status } from "../api/types";

function live(over: Partial<LiveGraph["boot"]>): LiveGraph {
  return {
    boot: { live: false, base: "", repo: "", ...over },
    status: initial<Status>(),
    runs: initial(NO_RUNS),
    showSkipped: false,
    setShowSkipped: vi.fn(),
    chooseRepo: vi.fn(),
    retry: vi.fn(),
  };
}

afterEach(cleanup);

describe("which page the bundle draws", () => {
  it("static mode draws the inlined graph and no live chrome at all", () => {
    const { container } = render(<Panels live={live({})} />);
    expect(container.innerHTML).toBe("");
  });

  it("static mode never renders a spinner that no poll can ever end", () => {
    render(<Panels live={live({})} />);
    expect(screen.queryByText(/Loading/)).toBeNull();
  });

  it("the no-repo page draws the switcher over an explicit empty state, not a run stream", () => {
    const boot = live({ live: true, base: "/", repo: "" });
    render(<Panels live={{ ...boot, status: { ...boot.status, phase: "ready" } }} />);
    expect(screen.getByTestId("chrome")).not.toBeNull();
    expect(screen.getByText(/No repo chosen yet/)).not.toBeNull();
    expect(screen.queryByTestId("RunStream")).toBeNull();
  });

  it("a repo chosen draws the whole column", () => {
    const boot = live({ live: true, base: "/", repo: "/w" });
    render(<Panels live={{ ...boot, status: { ...boot.status, phase: "ready" } }} />);
    expect(screen.getByTestId("RunStream")).not.toBeNull();
    expect(screen.getByTestId("RefinementList")).not.toBeNull();
    expect(screen.getByTestId("FlowPanel")).not.toBeNull();
  });
});
