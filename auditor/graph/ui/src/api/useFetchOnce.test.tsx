import { describe, it, expect, vi, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useFetchOnce } from "./useFetchOnce";

function ok(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function serve(...answers: (() => Promise<Response>)[]) {
  let call = 0;
  const fetcher = vi.fn((url: string) => {
    void url;
    return answers[Math.min(call++, answers.length - 1)]();
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

afterEach(() => vi.unstubAllGlobals());

describe("the fetch every panel off the poll shares", () => {
  it("asks once on mount and hands back what it was served", async () => {
    const fetcher = serve(() => Promise.resolve(ok({ ok: 1 })));
    const { result } = renderHook(() => useFetchOnce<{ ok: number }>("/api/x"));
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));
    expect(result.current.state.data).toEqual({ ok: 1 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("retry issues a second request, which is the whole reason the affordance exists", async () => {
    const fetcher = serve(
      () => Promise.reject(new Error("connection refused")),
      () => Promise.resolve(ok({ ok: 2 })),
    );
    const { result } = renderHook(() => useFetchOnce<{ ok: number }>("/api/x"));
    await waitFor(() => expect(result.current.state.phase).toBe("error"));
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.state.data).toEqual({ ok: 2 });
  });

  it("a retry that fails again is an error, never a spinner nothing can end", async () => {
    const fetcher = serve(() => Promise.reject(new Error("still down")));
    const { result } = renderHook(() => useFetchOnce<unknown>("/api/x"));
    await waitFor(() => expect(result.current.state.phase).toBe("error"));
    act(() => result.current.retry());
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.state.phase).toBe("error"));
    expect(result.current.state.error).toContain("still down");
  });

  it("a failed refetch over data we already have keeps drawing it and says so", async () => {
    serve(
      () => Promise.resolve(ok({ ok: 1 })),
      () => Promise.reject(new Error("gone")),
    );
    const { result } = renderHook(() => useFetchOnce<{ ok: number }>("/api/x"));
    await waitFor(() => expect(result.current.state.phase).toBe("ready"));
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.state.phase).toBe("stale"));
    expect(result.current.state.data).toEqual({ ok: 1 });
  });

  it("a url change is a new question, so it is asked", async () => {
    const fetcher = serve(() => Promise.resolve(ok({ ok: 1 })));
    const { rerender } = renderHook(({ url }) => useFetchOnce<unknown>(url), {
      initialProps: { url: "/api/x" },
    });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    rerender({ url: "/api/y" });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(fetcher.mock.calls[1][0]).toBe("/api/y");
  });

  it("a panel that has nothing to ask about yet asks nothing", async () => {
    const fetcher = serve(() => Promise.resolve(ok({})));
    renderHook(() => useFetchOnce<unknown>("/api/flow?symbol=", false));
    await new Promise((done) => setTimeout(done, 10));
    expect(fetcher).not.toHaveBeenCalled();
  });
});
