/** One conditional GET. `value` is null on a 304, which is what keeps the last good data. */
export interface Fetched<T> {
  value: T | null;
  etag: string;
}

export async function getJson<T>(url: string, etag: string): Promise<Fetched<T>> {
  const headers: HeadersInit = etag ? { "If-None-Match": etag } : {};
  const response = await fetch(url, { headers, credentials: "omit" });
  if (response.status === 304) return { value: null, etag };
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(body.error || `HTTP ${response.status}`);
  }
  return { value: (await response.json()) as T, etag: response.headers.get("ETag") ?? "" };
}
