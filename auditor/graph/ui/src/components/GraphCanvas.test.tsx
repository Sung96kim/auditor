import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import GraphCanvas, { hasRoom } from "./GraphCanvas";
import { sample } from "../sample";

/** jsdom lays nothing out, so every element measures zero: the guard's own condition. */
function draw() {
  return render(
    <GraphCanvas
      payload={sample}
      view={{ mode: "overview" }}
      onSelect={vi.fn()}
      onDrill={vi.fn()}
      onFocus={vi.fn()}
      onBackground={vi.fn()}
    />,
  );
}

afterEach(cleanup);

describe("whether a container is worth building a renderer in", () => {
  it.each([
    [600, 400, true],
    [0, 400, false],
    [600, 0, false],
    [0, 0, false],
  ])("%s by %s is %s", (clientWidth, clientHeight, expected) => {
    expect(hasRoom({ clientWidth, clientHeight })).toBe(expected);
  });
});

describe("a canvas with no room to draw in", () => {
  it("says so rather than throwing out of sigma's render loop", async () => {
    expect(() => draw()).not.toThrow();
    expect((await screen.findByRole("status")).textContent).toContain(
      "No room to draw the graph",
    );
  });

  it("names what would give it room, so the message is not a dead end", async () => {
    draw();
    expect((await screen.findByRole("status")).textContent).toContain("widen the window");
  });

  it("logs nothing, so the page is not carrying a caught throw it did not report", async () => {
    const failed = vi.fn();
    const original = console.error;
    console.error = failed;
    try {
      draw();
      await screen.findByRole("status");
      expect(failed).not.toHaveBeenCalled();
    } finally {
      console.error = original;
    }
  });
});
