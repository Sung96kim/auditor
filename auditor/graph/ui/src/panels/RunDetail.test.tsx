import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import RunDetail from "./RunDetail";
import type { RunDetailView } from "../api/types";

const VIEW: RunDetailView = {
  run: null,
  prompt: "walk the call graph from build and propose the edges the static pass could not resolve",
  tool_trace: [{ tool: "graph_search", ts: 1, detail: "query=build limit=20" }],
  refinements: [],
  trials: [],
  assessment: null,
};

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
