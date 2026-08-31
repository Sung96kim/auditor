import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import RunDetail from "./RunDetail";
import { refinementRow, runDetail, toolCall } from "../api/wire.fixture";

const VIEW = runDetail({
  run: null,
  prompt:
    "walk the call graph from build and propose the edges the static pass could not resolve",
  tool_trace: [toolCall({ tool: "graph_search", detail: "query=build limit=20" })],
  refinements: [
    refinementRow({
      kind: "annotate_node",
      tier: "C",
      status: "rejected",
      src: null,
      dst: null,
      edge_kind: null,
      node_id: "cli/main.py::_hidden",
      reason: "the symbol does not exist",
      confidence: 0.2,
    }),
  ],
  trials: [],
  assessment: null,
});

function serve() {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(VIEW), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
  const onClose = vi.fn();
  render(<RunDetail base="/" repo="/w" runId="3f2a1b9c44de4c7f" onClose={onClose} />);
  return onClose;
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the run detail", () => {
  it("the close control is named, so it is not a lowercase word only a mouse can find", async () => {
    const onClose = serve();
    const close = await screen.findByRole("button", { name: "Close run detail" });
    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("a prompt is penned in and scrolls, so a long one cannot stretch the column", async () => {
    serve();
    const prompt = await screen.findByText(/walk the call graph/);
    expect(prompt.style.maxHeight).toBe("120px");
    expect(prompt.style.overflowY).toBe("auto");
    expect(prompt.style.whiteSpace).toBe("pre-wrap");
  });

  it("a trace line wraps inside the panel rather than pushing it sideways", async () => {
    serve();
    const call = await screen.findByText(/query=build/);
    expect(call.style.overflowWrap).toBe("anywhere");
  });
});

describe("what a run detail does when its own fetch fails", () => {
  it("Retry asks again rather than dismissing the panel it was offered on", async () => {
    let call = 0;
    const fetcher = vi.fn(() =>
      call++ === 0
        ? Promise.reject(new Error("connection refused"))
        : Promise.resolve(
            new Response(JSON.stringify(VIEW), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          ),
    );
    vi.stubGlobal("fetch", fetcher);
    const onClose = vi.fn();
    render(<RunDetail base="/" repo="/w" runId="3f2a1b9c44de4c7f" onClose={onClose} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(onClose).not.toHaveBeenCalled();
    expect(await screen.findByText(/walk the call graph/)).not.toBeNull();
  });
});

describe("what a run detail says when a row has no edge to show", () => {
  it("a node refinement names its node, rather than drawing a dash moving to a dash", async () => {
    serve();
    expect((await screen.findByText(/cli\/main.py::_hidden/)).textContent).toContain(
      "[C] cli/main.py::_hidden",
    );
    expect(screen.queryByText(/- to -/)).toBeNull();
  });

  it("an empty tuning list reads inline, like every other empty group in the box", async () => {
    serve();
    const none = await screen.findByText(/S11 is what writes a tuning row/);
    expect(none.textContent).toBe("none, S11 is what writes a tuning row");
    expect(none.tagName).toBe("SPAN");
  });
});
