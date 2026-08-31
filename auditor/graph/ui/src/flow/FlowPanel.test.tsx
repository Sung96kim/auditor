import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import FlowPanel, { origin } from "./FlowPanel";
import type { Placed } from "./tree";
import type { FlowNode } from "../api/types";

function node(id: string, over: Partial<FlowNode> = {}): FlowNode {
  return {
    id,
    kind: "function",
    edge: "calls",
    source: "static",
    depth: 0,
    seen_ref: false,
    cycle: false,
    stopped: false,
    hub: null,
    unresolved: [],
    children: [],
    ...over,
  };
}

const ROOT = node("a.py::build", {
  children: [
    node("b.py::write", { depth: 1 }),
    node("c.py::collect", {
      depth: 1,
      hub: { count: 12, kind: "calls", collapsed: true },
      children: [node("c.py::one", { depth: 2 })],
    }),
  ],
});

function serve() {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            symbol: "build",
            flow: { root: ROOT, direction: "out", truncated: false },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    ),
  );
  return render(<FlowPanel base="/" repo="/w" />);
}

async function walked() {
  serve();
  fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "build" } });
  return screen.findByTitle("c.py::collect");
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the flow panel", () => {
  it("the panel with no symbol yet reads as a sentence, not as a stitched-together one", () => {
    serve();
    expect(screen.getByRole("status").textContent).toContain("No flow yet");
  });

  it("only a hub is a control: a plain node in the walk is read, not pressed", async () => {
    const hub = await walked();
    expect(hub.tagName).toBe("BUTTON");
    expect(screen.getByTitle("b.py::write").tagName).toBe("DIV");
  });

  it("a collapsed hub says how many it is holding, and says it is closed", async () => {
    const hub = await walked();
    expect(hub.getAttribute("aria-expanded")).toBe("false");
    expect(hub.textContent).toBe("collect+12");
  });

  it("opening a hub drops the fan count and reports itself open", async () => {
    const hub = await walked();
    fireEvent.click(hub);
    const opened = screen.getByTitle("c.py::collect");
    expect(opened.getAttribute("aria-expanded")).toBe("true");
    expect(opened.textContent).toBe("collect");
    expect(screen.getByTitle("c.py::one")).not.toBeNull();
  });

  it("the direction toggle reports which way the walk runs", () => {
    serve();
    expect(screen.getByRole("button", { name: "out" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "in" }));
    expect(screen.getByRole("button", { name: "in" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "out" }).getAttribute("aria-pressed")).toBe("false");
  });
});

function at(x: number, y: number): Placed {
  return {
    key: `${x}-${y}`,
    id: "a.py::one",
    depth: 0,
    edge: "calls",
    unresolved: false,
    external: false,
    hub: null,
    collapsed: false,
    parent: null,
    x,
    y,
  };
}

describe("where the walk is drawn", () => {
  it("the corner of the drawing is the corner of the panel, not dagre's own centre", () => {
    expect(origin([at(90, 14), at(290, 74)])).toEqual({ x: 90, y: 14 });
  });

  it("an empty walk has nothing to shift", () => {
    expect(origin([])).toEqual({ x: 0, y: 0 });
  });

  it("the root of a rendered walk starts at the edge, with no margin of nothing before it", async () => {
    const hub = await walked();
    const lefts = [...document.querySelectorAll("[title$='::build'], [title$='::write']")].map(
      (el) => (el as HTMLElement).style.left,
    );
    expect(lefts).toContain("0px");
    expect(hub).not.toBeNull();
  });
});
