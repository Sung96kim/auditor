import { useCallback, useEffect, useRef, useState } from "react";
import { getJson, isRefusal } from "./client";
import { failed, initial, received, retryDelay, type PollState } from "./poll";
import { readBootstrap, type Bootstrap } from "./bootstrap";
import type { RunsView, Status } from "./types";

/** The answer for a page that will never ask: ready and empty, so no panel waits on a poll. */
export const NO_RUNS: RunsView = {
  log: { runs: [], hidden_count: 0, run_count: 0, truncated: false },
};

export interface LiveGraph {
  boot: Bootstrap;
  status: PollState<Status>;
  runs: PollState<RunsView>;
  /** true while the reader has asked to see collapsed rows, which is `skipped=1` on the wire. */
  showSkipped: boolean;
  setShowSkipped: (on: boolean) => void;
  /** the switcher navigates rather than fetching a 17 MB graph over XHR (P2). */
  chooseRepo: (repo: string) => void;
  retry: () => void;
}

/** Spec 12.1's 3 s ETag poll of `/api/status` and `/api/runs`, and nothing else on that cycle. */
export function useLiveGraph(): LiveGraph {
  const [boot] = useState<Bootstrap>(() => readBootstrap(window));
  const [status, setStatus] = useState<PollState<Status>>(() => initial<Status>());
  const [runs, setRuns] = useState<PollState<RunsView>>(() =>
    boot.live && boot.repo ? initial<RunsView>() : initial<RunsView>(NO_RUNS),
  );
  const [showSkipped, setShowSkipped] = useState(false);
  const [tick, setTick] = useState(0);
  const tags = useRef({ status: "", runs: "" });
  const attempts = useRef({ status: 0, runs: 0 });

  const retry = useCallback(() => {
    attempts.current = { status: 0, runs: 0 };
    setTick((n) => n + 1);
  }, []);

  const chooseRepo = useCallback((repo: string) => {
    const next = new URL(window.location.href);
    next.searchParams.set("repo", repo);
    window.location.assign(next.toString());
  }, []);

  useEffect(() => {
    if (!boot.live) return;
    let alive = true;
    let timer = 0;

    const one = async () => {
      try {
        const got = await getJson<Status>(`${boot.base}api/status`, tags.current.status);
        // inside the guard: a cycle torn down by a filter change or a retry must not put its
        // own tag back over the new one's, which costs a body and can mask the backoff
        if (alive) {
          tags.current.status = got.etag;
          attempts.current.status = 0;
          setStatus((prev) => received(prev, got.value));
        }
      } catch (err) {
        attempts.current.status += 1;
        if (alive) setStatus((prev) => failed(prev, String(err), isRefusal(err)));
      }
      if (boot.repo) {
        const query = new URLSearchParams({ repo: boot.repo });
        if (showSkipped) query.set("skipped", "1");
        try {
          const got = await getJson<RunsView>(`${boot.base}api/runs?${query}`, tags.current.runs);
          if (alive) {
            tags.current.runs = got.etag;
            attempts.current.runs = 0;
            setRuns((prev) => received(prev, got.value));
          }
        } catch (err) {
          attempts.current.runs += 1;
          if (alive) setRuns((prev) => failed(prev, String(err), isRefusal(err)));
        }
      }
      // the backoff is read from the worse of the two surfaces, or a dead daemon is polled
      // every 3 s for as long as the tab lives, which is the thing P13 exists to prevent
      const worst = Math.max(attempts.current.status, attempts.current.runs);
      if (alive) timer = window.setTimeout(one, retryDelay(worst));
    };

    void one();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [boot, showSkipped, tick]);

  return { boot, status, runs, showSkipped, setShowSkipped, chooseRepo, retry };
}
