import { describe, it, expect, vi, afterEach } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { NARROW, useMediaQuery } from "./layout";

type Listener = (event: MediaQueryListEvent) => void;

/** A media engine with a switch on it, so a resize is something a test can actually perform. */
function engine(matches: boolean) {
  const listeners = new Set<Listener>();
  const list = {
    matches,
    media: NARROW,
    addEventListener: (_: string, fn: Listener) => listeners.add(fn),
    removeEventListener: (_: string, fn: Listener) => listeners.delete(fn),
  };
  vi.stubGlobal("matchMedia", vi.fn(() => list));
  return (now: boolean) => {
    list.matches = now;
    act(() => listeners.forEach((fn) => fn({ matches: now } as MediaQueryListEvent)));
  };
}

function Reader() {
  return <span data-testid="reading">{String(useMediaQuery(NARROW))}</span>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("reading the viewport the layout branches on", () => {
  it("reports what the engine says at first paint, not a guess", () => {
    engine(true);
    render(<Reader />);
    expect(screen.getByTestId("reading").textContent).toBe("true");
  });

  it("follows the window across the breakpoint rather than freezing at mount", () => {
    const resize = engine(false);
    render(<Reader />);
    expect(screen.getByTestId("reading").textContent).toBe("false");
    resize(true);
    expect(screen.getByTestId("reading").textContent).toBe("true");
  });

  it("with no media engine at all it reads false, which is the wide layout", () => {
    vi.stubGlobal("matchMedia", undefined);
    render(<Reader />);
    expect(screen.getByTestId("reading").textContent).toBe("false");
  });

  it("drops its listener on unmount, so a torn-down page is not still being told", () => {
    const listeners: Listener[] = [];
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: false,
        media: NARROW,
        addEventListener: (_: string, fn: Listener) => listeners.push(fn),
        removeEventListener: (_: string, fn: Listener) => {
          listeners.splice(listeners.indexOf(fn), 1);
        },
      })),
    );
    const { unmount } = render(<Reader />);
    expect(listeners).toHaveLength(1);
    unmount();
    expect(listeners).toHaveLength(0);
  });
});
