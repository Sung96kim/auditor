import { describe, it, expect, vi, afterEach } from "vitest";
import { RequestError, getJson, isRefusal } from "./client";

function answer(init: {
  status: number;
  body?: unknown;
  etag?: string;
  statusText?: string;
}): Response {
  return {
    status: init.status,
    ok: init.status >= 200 && init.status < 300,
    statusText: init.statusText ?? "",
    headers: { get: (name: string) => (name === "ETag" ? (init.etag ?? null) : null) },
    json: async () => init.body,
  } as unknown as Response;
}

afterEach(() => vi.unstubAllGlobals());

describe("the conditional GET the poll is built on", () => {
  it("a 200 carries the body and the tag the next request will send", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => answer({ status: 200, body: { a: 1 }, etag: 'W/"t1"' })));
    expect(await getJson<{ a: number }>("/api/status", "")).toEqual({ value: { a: 1 }, etag: 'W/"t1"' });
  });

  it("a first request sends no If-None-Match, and a later one sends the tag it holds", async () => {
    const sent: Record<string, string>[] = [];
    const fetcher = vi.fn(async (_url: string, init: RequestInit) => {
      sent.push(init.headers as Record<string, string>);
      return answer({ status: 200, body: {}, etag: 'W/"t1"' });
    });
    vi.stubGlobal("fetch", fetcher);
    await getJson("/api/status", "");
    await getJson("/api/status", 'W/"t1"');
    expect(sent).toEqual([{}, { "If-None-Match": 'W/"t1"' }]);
  });

  it("a 304 answers null and keeps the tag, which is what keeps the last good data", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => answer({ status: 304 })));
    expect(await getJson("/api/runs", 'W/"t1"')).toEqual({ value: null, etag: 'W/"t1"' });
  });

  it("a 400 throws the field name the daemon put in the body, never `HTTP 400`", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => answer({ status: 400, body: { error: "limit must be a whole number" } })));
    await expect(getJson("/api/runs?limit=abc", "")).rejects.toThrow("limit must be a whole number");
  });

  it("a failure with no JSON body still names the status rather than throwing a parse error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      status: 502,
      ok: false,
      statusText: "Bad Gateway",
      headers: { get: () => null },
      json: async () => {
        throw new Error("not json");
      },
    } as unknown as Response)));
    await expect(getJson("/api/status", "")).rejects.toThrow("Bad Gateway");
  });

  it("a 4xx carries its status, so the page can tell a refusal from an outage", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => answer({ status: 400, body: { error: "bad limit" } })));
    const thrown = await getJson("/api/runs?limit=abc", "").catch((err) => err);
    expect(thrown).toBeInstanceOf(RequestError);
    expect(thrown.status).toBe(400);
    expect(isRefusal(thrown)).toBe(true);
  });

  it("a 5xx and a dead socket are not refusals, because retrying either one can work", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => answer({ status: 503, body: { error: "down" } })));
    expect(isRefusal(await getJson("/api/status", "").catch((err) => err))).toBe(false);
    expect(isRefusal(new TypeError("Failed to fetch"))).toBe(false);
  });

  it("a 200 with no ETag header leaves the held tag empty rather than sending `null`", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => answer({ status: 200, body: {} })));
    expect((await getJson("/api/status", "")).etag).toBe("");
  });
});
