import { useCallback, useEffect, useState } from "react";
import { getJson, isRefusal } from "./client";
import { failed, initial, received, type PollState } from "./poll";

export interface Fetched<T> {
  state: PollState<T>;
  /** issues a request. Putting the state back to `loading` never did, so the panel sat there. */
  retry: () => void;
}

/** One `GET` on mount, on every URL change and on every retry: the three panels off the poll.
 *
 * The attempt counter is the whole point. Three panels each hand-rolled this block and two of
 * them retried by resetting their own state, which changed no effect dependency and issued no
 * request, so pressing Retry replaced the error with a spinner that could never end.
 */
export function useFetchOnce<T>(url: string, enabled = true): Fetched<T> {
  const [state, setState] = useState<PollState<T>>(() => initial<T>());
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    getJson<T>(url, "")
      .then((got) => {
        if (alive) setState((prev) => received(prev, got.value));
      })
      .catch((err) => {
        if (alive) setState((prev) => failed(prev, String(err), isRefusal(err)));
      });
    return () => {
      alive = false;
    };
  }, [url, attempt, enabled]);

  const retry = useCallback(() => {
    // last good data stays on screen under the reconnect banner; a first failure goes back to
    // the loading track, which is bounded by the request this same call is about to issue
    setState((prev) => (prev.data === null ? initial<T>() : prev));
    setAttempt((n) => n + 1);
  }, []);

  return { state, retry };
}
