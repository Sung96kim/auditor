import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import Panels from "./Panels";
import { initial, received } from "../api/poll";
import { NO_RUNS, type LiveGraph } from "../api/useLiveGraph";
import { repo as aRepo, status as aStatus } from "../api/wire.fixture";
import type { Status } from "../api/types";

const STATUS = aStatus({ repos: [aRepo({ repo: "/w" })] });

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

/** A live page whose status poll has answered, which is the only state that draws the chrome. */
function answered(repo: string): LiveGraph {
  const boot = live({ live: true, base: "/", repo });
  return { ...boot, status: received(boot.status, STATUS) };
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ runners: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

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
    render(<Panels live={answered("")} />);
    // a real status, not an empty one: with `data` null the card draws none of what this names
    screen.getByTestId("chrome");
    expect(screen.getByLabelText("Repository")).not.toBeNull();
    expect(screen.queryAllByRole("progressbar")).toHaveLength(0);
    expect(screen.getByText(/No repo chosen yet/)).not.toBeNull();
    expect(screen.queryByTestId("RunStream")).toBeNull();
  });

  it("a repo chosen draws the whole column", () => {
    render(<Panels live={answered("/w")} />);
    screen.getByTestId("RunStream");
    screen.getByTestId("RefinementList");
    screen.getByTestId("FlowPanel");
    expect(screen.getAllByRole("progressbar")).toHaveLength(2);
  });
});

const CARDS = ["chrome", "RunStream", "RefinementList", "FlowPanel"];

describe("the chrome every card in the column is drawn with", () => {
  it("one card, so the radius cannot drift panel by panel", () => {
    render(<Panels live={answered("/w")} />);
    const radii = new Set(CARDS.map((id) => screen.getByTestId(id).style.borderRadius));
    expect(radii).toEqual(new Set(["12px"]));
  });

  it("each panel names itself in the strip above its body, not in with its content", () => {
    render(<Panels live={answered("/w")} />);
    const strips = CARDS.map((id) => screen.getByTestId(id).firstElementChild);
    expect(strips.map((el) => el?.textContent)).toEqual([
      "Observerobserving",
      "Runs",
      "Refinements",
      "Flow",
    ]);
  });

  it("beside the canvas the column keeps its width when the panels in it grow", () => {
    const { container } = render(<Panels live={answered("/w")} />);
    const column = container.querySelector("aside") as HTMLElement;
    expect(column.style.flexShrink).toBe("0");
    expect(column.style.width).toBe("340px");
  });

  it("under the canvas it is a full-width shelf, taking no width off the graph at all", () => {
    const { container } = render(<Panels live={answered("/w")} narrow />);
    const column = container.querySelector("aside") as HTMLElement;
    expect(column.style.width).toBe("100%");
    expect(column.style.flexBasis).toBe("");
    expect(column.style.maxHeight).toBe("45%");
  });

  it("both layouts scroll inside themselves rather than growing the page", () => {
    for (const narrow of [false, true]) {
      cleanup();
      const { container } = render(<Panels live={answered("/w")} narrow={narrow} />);
      const column = container.querySelector("aside") as HTMLElement;
      expect(column.style.overflowY).toBe("auto");
      expect(column.style.minHeight).toBe("0px");
    }
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
