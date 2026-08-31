import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
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

const CARDS = ["chrome", "RunStream", "RefinementList", "FlowPanel"];

describe("the chrome every card in the column is drawn with", () => {
  it("one card, so the radius cannot drift panel by panel", () => {
    const boot = live({ live: true, base: "/", repo: "/w" });
    render(<Panels live={{ ...boot, status: { ...boot.status, phase: "ready" } }} />);
    const radii = new Set(CARDS.map((id) => screen.getByTestId(id).style.borderRadius));
    expect(radii).toEqual(new Set(["12px"]));
  });

  it("each panel names itself in the strip above its body, not in with its content", () => {
    const boot = live({ live: true, base: "/", repo: "/w" });
    render(<Panels live={{ ...boot, status: { ...boot.status, phase: "ready" } }} />);
    const strips = CARDS.map((id) => screen.getByTestId(id).firstElementChild);
    expect(strips.map((el) => el?.textContent)).toEqual([
      "Observer",
      "Runs",
      "Refinements",
      "Flow",
    ]);
  });

  it("a failed first poll still says which panel failed, rather than swapping the card for an error", () => {
    const boot = live({ live: true, base: "/", repo: "/w" });
    const status = { ...boot.status, phase: "error" as const, error: "connection refused" };
    render(<Panels live={{ ...boot, status }} />);
    const chrome = within(screen.getByTestId("chrome"));
    expect(chrome.getByText("Observer")).not.toBeNull();
    expect(chrome.getByRole("alert").textContent).toContain("connection refused");
  });
});
