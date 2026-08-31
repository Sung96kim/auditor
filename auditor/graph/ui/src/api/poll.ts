/** The state every polled surface is in, so EMPTY, LOADING and ERROR are one vocabulary. */
export type Phase = "loading" | "ready" | "stale" | "error";

export interface PollState<T> {
  phase: Phase;
  data: T | null;
  error: string;
  attempts: number;
}

export const POLL_MS = 3000;
const BACKOFF_MS = [3000, 6000, 12000, 30000];

export function initial<T>(seed: T | null = null): PollState<T> {
  return { phase: seed === null ? "loading" : "ready", data: seed, error: "", attempts: 0 };
}

/** A 200 carries a value; a 304 carries null and the last good data stands. */
export function received<T>(prev: PollState<T>, value: T | null): PollState<T> {
  return {
    phase: "ready",
    data: value === null ? prev.data : value,
    error: "",
    attempts: 0,
  };
}

/** A failed poll never blanks the panel: it reconnects over the last good data when there is any. */
export function failed<T>(prev: PollState<T>, message: string): PollState<T> {
  return {
    phase: prev.data === null ? "error" : "stale",
    data: prev.data,
    error: message,
    attempts: prev.attempts + 1,
  };
}

/** How long to wait before the next try, so a stopped daemon is not polled 20 times a minute. */
export function retryDelay(attempts: number): number {
  if (attempts <= 0) return POLL_MS;
  return BACKOFF_MS[Math.min(attempts, BACKOFF_MS.length) - 1];
}
