import { describe, it, expect } from "vitest";
import { POLL_MS, failed, initial, received, retryDelay } from "./poll";

describe("the polled-surface state machine", () => {
  it("starts in LOADING with nothing to draw", () => {
    const state = initial<number>();
    expect(state.phase).toBe("loading");
    expect(state.data).toBeNull();
  });

  it("starts READY when the page was bootstrapped with a first paint", () => {
    expect(initial(7).phase).toBe("ready");
  });

  it("a 200 replaces the data and clears the error", () => {
    const state = received(failed(initial(1), "boom"), 2);
    expect(state).toEqual({
      phase: "ready",
      data: 2,
      error: "",
      attempts: 0,
      at: state.at,
    });
    expect(state.at).toBeGreaterThan(0);
  });

  it("a 304 keeps the last good data rather than blanking the panel", () => {
    const state = received(initial(1), null);
    expect(state.data).toBe(1);
    expect(state.phase).toBe("ready");
  });

  it("a 304 keeps the stamp of the body it answers for, so a clock in it is not called fresh", () => {
    const first = { ...initial(1), at: 1000 };
    expect(received(first, null).at).toBe(1000);
    expect(received(first, 2).at).toBeGreaterThan(1000);
  });

  it("a failed poll keeps the stamp of the data it is still drawing", () => {
    const first = { ...initial(1), at: 1000 };
    expect(failed(first, "boom").at).toBe(1000);
  });

  it("a failed poll over existing data reconnects instead of showing a spinner", () => {
    const state = failed(initial(1), "network down");
    expect(state.phase).toBe("stale");
    expect(state.data).toBe(1);
    expect(state.error).toBe("network down");
  });

  it("a failed first poll is an ERROR state, never a stuck spinner", () => {
    const state = failed(initial<number>(), "network down");
    expect(state.phase).toBe("error");
    expect(state.data).toBeNull();
  });

  it("a refusal is its own phase, whether or not there is data under it", () => {
    expect(failed(initial<number>(), "bad limit", true).phase).toBe("refused");
    const over = failed(initial(1), "bad limit", true);
    expect(over.phase).toBe("refused");
    expect(over.data).toBe(1);
  });

  it("an ordinary failure is unchanged, so only a refusal takes the new phase", () => {
    expect(failed(initial(1), "network down", false).phase).toBe("stale");
    expect(failed(initial<number>(), "network down").phase).toBe("error");
  });

  it("repeated failures back off instead of polling a dead daemon every 3 s", () => {
    expect(retryDelay(0)).toBe(POLL_MS);
    const delays = [1, 2, 3, 4, 9].map(retryDelay);
    expect(delays).toEqual([3000, 6000, 12000, 30000, 30000]);
    expect(delays.every((d, i) => i === 0 || d >= delays[i - 1])).toBe(true);
  });

  it("one success resets the backoff", () => {
    const recovered = received(failed(failed(initial(1), "a"), "b"), 2);
    expect(recovered.attempts).toBe(0);
    expect(retryDelay(recovered.attempts)).toBe(POLL_MS);
  });
});
