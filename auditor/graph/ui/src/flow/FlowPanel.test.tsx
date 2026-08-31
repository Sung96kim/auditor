import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import FlowPanel, { origin } from "./FlowPanel";
import type { Placed } from "./tree";
import { flowNode as node, flowView, hubMark } from "../api/wire.fixture";

const ROOT = node({
  id: "a.py::build",
  children: [
    node({ id: "b.py::write", depth: 1, edge: "calls" }),
    node({
      id: "c.py::collect",
      depth: 1,
      edge: "calls",
      hub: hubMark({ count: 12, kind: "expansion", collapsed: false }),
      children: [node({ id: "c.py::one", depth: 2, edge: "calls" })],
    }),
  ],
});

function answer(): Response {
  return new Response(JSON.stringify(flowView({ symbol: "build", flow: { root: ROOT, direction: "out", truncated: false } })), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function serve(...answers: (() => Promise<Response>)[]) {
  let call = 0;
  const fetcher = vi.fn((url: string) => {
    void url;
    return (answers.length ? answers[Math.min(call++, answers.length - 1)] : answer)();
  });
  vi.stubGlobal("fetch", fetcher as unknown as typeof fetch);
  render(<FlowPanel base="/" repo="/w" />);
  return fetcher;
}

async function walked() {
  const fetcher = serve();
  fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "build" } });
  return { hub: await screen.findByTitle("c.py::collect"), fetcher };
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
    const { hub } = await walked();
    expect(hub.tagName).toBe("BUTTON");
    expect(screen.getByTitle("b.py::write").tagName).toBe("DIV");
  });

  it("a collapsed hub says how many it is holding, and says it is closed", async () => {
    const { hub } = await walked();
    expect(hub.getAttribute("aria-expanded")).toBe("false");
    expect(hub.textContent).toBe("collect+12");
  });

  it("opening a hub drops the fan count and reports itself open", async () => {
    const { hub } = await walked();
    fireEvent.click(hub);
    const opened = screen.getByTitle("c.py::collect");
    expect(opened.getAttribute("aria-expanded")).toBe("true");
    expect(opened.textContent).toBe("collect");
    expect(screen.getByTitle("c.py::one")).not.toBeNull();
  });

  it("opening a hub asks the daemon to walk past it, which is the only way it has children", async () => {
    const { hub, fetcher } = await walked();
    const before = fetcher.mock.calls.length;
    fireEvent.click(hub);
    await waitFor(() => expect(fetcher.mock.calls.length).toBe(before + 1));
    const last = fetcher.mock.calls[fetcher.mock.calls.length - 1];
    expect(String(last[0])).toContain("expand_hubs=1");
    expect(screen.getByTitle("c.py::one")).not.toBeNull();
  });

  it("a failed refetch after a success reconnects over the walk rather than going blank", async () => {
    serve(
      () => Promise.resolve(answer()),
      () => Promise.reject(new Error("connection refused")),
    );
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "build" } });
    await screen.findByTitle("c.py::collect");
    fireEvent.click(screen.getByRole("button", { name: "in" }));
    expect(await screen.findByText(/Reconnecting to the observer/)).not.toBeNull();
    expect(screen.getByTitle("c.py::collect")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Retry now" })).not.toBeNull();
  });

  it("typing a symbol is one walk, not one walk per character", async () => {
    const fetcher = serve();
    const box = screen.getByLabelText("Symbol");
    for (const value of ["b", "bu", "bui", "buil", "build"]) {
      fireEvent.change(box, { target: { value } });
    }
    await screen.findByTitle("c.py::collect");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(String(fetcher.mock.calls[0][0])).toContain("symbol=build");
  });

  it("every node in the walk carries its full id as its name, not only as a tooltip", async () => {
    await walked();
    expect(screen.getByLabelText("b.py::write").tagName).toBe("DIV");
    expect(screen.getByRole("button", { name: "c.py::collect" })).not.toBeNull();
  });

  it("a walk the server capped says so, rather than looking like the whole graph", async () => {
    serve(() =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            flowView({ symbol: "build", flow: { root: ROOT, direction: "out", truncated: true } }),
          ),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "build" } });
    await screen.findByTitle("c.py::collect");
    const strip = screen.getByTestId("FlowPanel").firstElementChild;
    expect(strip?.textContent).toBe("Flow3, capped");
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
    const { hub } = await walked();
    const lefts = [...document.querySelectorAll("[title$='::build'], [title$='::write']")].map(
      (el) => (el as HTMLElement).style.left,
    );
    expect(lefts).toContain("0px");
    expect(hub).not.toBeNull();
  });
});
