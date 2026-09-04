import { describe, it, expect, vi, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useDebounced } from "./useDebounced";

afterEach(() => vi.useRealTimers());

describe("the debounce the symbol box runs on", () => {
  it("holds the first value until the delay has passed with nothing new", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value }) => useDebounced(value, 300), {
      initialProps: { value: "b" },
    });
    rerender({ value: "bu" });
    rerender({ value: "bui" });
    expect(result.current).toBe("b");
    act(() => vi.advanceTimersByTime(300));
    expect(result.current).toBe("bui");
  });

  it("a keystroke inside the window restarts it rather than letting the old value through", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value }) => useDebounced(value, 300), {
      initialProps: { value: "b" },
    });
    rerender({ value: "bu" });
    act(() => vi.advanceTimersByTime(200));
    rerender({ value: "bui" });
    act(() => vi.advanceTimersByTime(200));
    expect(result.current).toBe("b");
    act(() => vi.advanceTimersByTime(100));
    expect(result.current).toBe("bui");
  });
});
