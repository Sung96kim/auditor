import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useLiveGraph } from "./useLiveGraph";
import { POLL_MS } from "./poll";

function ok(body: unknown, etag = 'W/"t"'): Response {
  return {
    status: 200,
    ok: true,
    statusText: "",
    headers: { get: (n: string) => (n === "ETag" ? etag : null) },
    json: async () => body,
  } as unknown as Response;
}

function live(repo: string): void {
  (window as unknown as Record<string, unknown>).__AUDITOR_OBSERVER__ = {
    live: true,
    base: "/",
    repo,
  };
}

beforeEach(() => vi.useFakeTimers());

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (window as unknown as Record<string, unknown>).__AUDITOR_OBSERVER__;
});

describe("the live hook", () => {
  it("a static page issues no request at all, because `graph serve` has no /api/*", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    renderHook(() => useLiveGraph());
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 4));
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("a static page's run stream is empty and ready, never a spinner that cannot end", () => {
    vi.stubGlobal("fetch", vi.fn());
    const { result } = renderHook(() => useLiveGraph());
    expect(result.current.boot.live).toBe(false);
    expect(result.current.runs.phase).toBe("ready");
    expect(result.current.runs.data!.log.runs).toEqual([]);
  });

  it("the daemon's no-repo page polls status and still never waits on a run stream", async () => {
    live("");
    const asked: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      asked.push(url);
      return ok({ repos: [] });
    }));
    const { result } = renderHook(() => useLiveGraph());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(asked).toEqual(["/api/status"]);
    expect(result.current.runs.phase).toBe("ready");
  });

  it("a live repo polls both routes every 3 s while they answer", async () => {
    live("/w");
    const fetcher = vi.fn(async () => ok({ repos: [] }));
    vi.stubGlobal("fetch", fetcher);
    renderHook(() => useLiveGraph());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS));
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("repeated failures back off rather than polling a dead daemon 20 times a minute", async () => {
    live("/w");
    const fetcher = vi.fn(async () => {
      throw new Error("down");
    });
    vi.stubGlobal("fetch", fetcher);
    renderHook(() => useLiveGraph());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(fetcher).toHaveBeenCalledTimes(2);
    // 3 s, then 6 s, then 12 s: 20 s buys three cycles of two. A flat 3 s would buy seven.
    await act(() => vi.advanceTimersByTimeAsync(20_000));
    expect(fetcher).toHaveBeenCalledTimes(6);
  });

  it("a failed first poll is an error state with a retry, never a stuck spinner", async () => {
    live("/w");
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("down");
    }));
    const { result } = renderHook(() => useLiveGraph());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.status.phase).toBe("error");
    expect(result.current.runs.phase).toBe("error");
  });

  it("one success resets the backoff, so a recovered daemon is polled at 3 s again", async () => {
    live("/w");
    let down = true;
    const fetcher = vi.fn(async () => {
      if (down) throw new Error("down");
      return ok({ repos: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    renderHook(() => useLiveGraph());
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(3000)); // second try, now 6 s out
    down = false;
    await act(() => vi.advanceTimersByTimeAsync(6000)); // third try succeeds
    const recovered = fetcher.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS));
    expect(fetcher.mock.calls.length).toBe(recovered + 2);
  });

  it("a 304 keeps the last good data rather than blanking the panel", async () => {
    live("/w");
    let first = true;
    vi.stubGlobal("fetch", vi.fn(async () => {
      if (first) {
        first = false;
        return ok({ repos: [{ repo: "/w" }] });
      }
      return { status: 304, ok: false, headers: { get: () => null } } as unknown as Response;
    }));
    const { result } = renderHook(() => useLiveGraph());
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 2));
    expect(result.current.status.phase).toBe("ready");
    expect(result.current.status.data!.repos).toHaveLength(1);
  });

  it("asking for collapsed rows puts `skipped=1` on the wire, which is what moves the tag", async () => {
    live("/w");
    const asked: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      asked.push(url);
      return ok({ repos: [] });
    }));
    const { result } = renderHook(() => useLiveGraph());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(asked[1]).toBe("/api/runs?repo=%2Fw");
    await act(async () => {
      result.current.setShowSkipped(true);
    });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(asked[asked.length - 1]).toBe("/api/runs?repo=%2Fw&skipped=1");
  });
});
