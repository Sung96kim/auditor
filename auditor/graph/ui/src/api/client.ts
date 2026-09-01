/** One conditional GET. `value` is null on a 304, which is what keeps the last good data. */
export interface Fetched<T> {
  value: T | null;
  etag: string;
}

/** A response the daemon answered with, rather than a request that never landed.
 *
 * The status is the whole point: a page that only had the message could not tell a permanent
 * refusal from an outage, and framed a 400 as something a Retry might fix.
 */
export class RequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "RequestError";
    this.status = status;
  }
}

/** Whether the daemon declined the request itself, which no amount of retrying can change. */
export function isRefusal(error: unknown): boolean {
  return error instanceof RequestError && error.status >= 400 && error.status < 500;
}

export async function getJson<T>(url: string, etag: string): Promise<Fetched<T>> {
  const headers: HeadersInit = etag ? { "If-None-Match": etag } : {};
  const response = await fetch(url, { headers, credentials: "omit" });
  if (response.status === 304) return { value: null, etag };
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: response.statusText }));
    throw new RequestError(body.error || `HTTP ${response.status}`, response.status);
  }
  return { value: (await response.json()) as T, etag: response.headers.get("ETag") ?? "" };
}
