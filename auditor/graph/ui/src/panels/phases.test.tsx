import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import Chrome from "./Chrome";
import RefinementList from "./RefinementList";
import RunDetail from "./RunDetail";
import RunStream from "./RunStream";
import FlowPanel from "../flow/FlowPanel";
import { failed, initial, received, type PollState } from "../api/poll";
import type { LiveGraph } from "../api/useLiveGraph";
import {
  flowView,
  refinementsView,
  runDetail,
  runRow,
  runsView,
  status as aStatus,
} from "../api/wire.fixture";

/** Two rows, so opening the second remounts the detail the first one left behind. */
const TWO = [runRow(), runRow({ run_id: "0000second00000", trigger_kind: "manual" })];
import type { Status } from "../api/types";

/** The four states the plan pins, in the words the shared components draw them in. */
const LOADING = /Loading /;
const FAILED = "Could not reach the observer";
const RECONNECTING = "Reconnecting to the observer";
const REFUSED = "The observer refused this request";

/** A daemon that answers with a status rather than failing to answer at all. */
function declines(status: number, error: string): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ error }), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** A fetch that answers, one that never answers, and one that fails, in that order per call. */
function serve(...answers: ("ok" | "down" | "never")[]): (body: unknown) => void {
  let call = 0;
  let payload: unknown = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      const answer = answers[Math.min(call++, answers.length - 1)];
      if (answer === "down") return Promise.reject(new Error("connection refused"));
      if (answer === "never") return new Promise<Response>(() => {});
      return Promise.resolve(json(payload));
    }),
  );
  return (body: unknown) => {
    payload = body;
  };
}

function graph(runs: PollState<LiveGraph["runs"]["data"]>): LiveGraph {
  return {
    boot: { live: true, base: "/", repo: "/w" },
    status: initial<Status>(),
    runs: runs as LiveGraph["runs"],
    showSkipped: false,
    setShowSkipped: vi.fn(),
    chooseRepo: vi.fn(),
    retry: vi.fn(),
  };
}

function statusIn(phase: "loading" | "ready" | "error" | "stale"): PollState<Status> {
  const ready = received(initial<Status>(), aStatus());
  if (phase === "loading") return initial<Status>();
  if (phase === "ready") return ready;
  if (phase === "stale") return failed(ready, "connection refused");
  return failed(initial<Status>(), "connection refused");
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the observer card in every state its poll can be in", () => {
  it("says the daemon is being read while the first poll is out", () => {
    serve("never");
    render(<Chrome status={statusIn("loading")} base="/" repo="/w" onChooseRepo={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText(LOADING)).not.toBeNull();
  });

  it("draws the switcher, both meters and the eval block once it has an answer", async () => {
    const set = serve("ok");
    set({ runners: [] });
    render(<Chrome status={statusIn("ready")} base="/" repo="/w/repo" onChooseRepo={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByLabelText("Repository")).not.toBeNull();
    expect(screen.getAllByRole("progressbar")).toHaveLength(2);
    expect(await screen.findAllByText("no eval yet")).toHaveLength(2);
  });

  it("a first poll that failed is an error with a retry, and no half-drawn chrome", () => {
    serve("never");
    render(<Chrome status={statusIn("error")} base="/" repo="/w" onChooseRepo={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByRole("alert").textContent).toContain(FAILED);
    expect(screen.queryByLabelText("Repository")).toBeNull();
  });

  it("a later poll that failed keeps the last good chrome under a reconnect banner", () => {
    serve("never");
    render(<Chrome status={statusIn("stale")} base="/" repo="/w/repo" onChooseRepo={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText(RECONNECTING)).not.toBeNull();
    expect(screen.getByLabelText("Repository")).not.toBeNull();
  });
});

describe("the run stream in every state its poll can be in", () => {
  it.each([
    ["loading", LOADING],
    ["error", FAILED],
    ["stale", RECONNECTING],
  ] as const)("%s draws its own word and nothing else's", (phase, words) => {
    serve("never");
    const ready = received(initial(runsView()), runsView());
    const state =
      phase === "loading"
        ? initial(null)
        : phase === "error"
          ? failed(initial(null), "connection refused")
          : failed(ready, "connection refused");
    render(<RunStream live={graph(state as never)} />);
    expect(screen.getByText(words)).not.toBeNull();
  });

  it("an answered poll draws the table, and never the empty state beside it", () => {
    serve("never");
    render(<RunStream live={graph(received(initial(null), runsView()) as never)} />);
    expect(screen.getAllByRole("row").length).toBeGreaterThan(1);
    expect(screen.queryByText("No runs yet")).toBeNull();
  });
});

describe("the refinement list in every state its fetch can be in", () => {
  it("draws its loading state until the fetch answers", () => {
    serve("never");
    render(<RefinementList base="/" repo="/w" />);
    expect(screen.getByText(LOADING)).not.toBeNull();
  });

  it("draws its rows once it has them", async () => {
    const set = serve("ok");
    set(refinementsView());
    render(<RefinementList base="/" repo="/w" />);
    expect(await screen.findByText(/active \(1\)/)).not.toBeNull();
  });

  it("a failed fetch is an error with a retry, never a spinner", async () => {
    serve("down");
    render(<RefinementList base="/" repo="/w" />);
    expect((await screen.findByRole("alert")).textContent).toContain(FAILED);
    expect(screen.queryByText(LOADING)).toBeNull();
  });

  it("has no reconnect state, because one answer is all it ever asks for", async () => {
    const set = serve("ok");
    set(refinementsView());
    const fetcher = globalThis.fetch as ReturnType<typeof vi.fn>;
    const { rerender } = render(<RefinementList base="/" repo="/w" />);
    await screen.findByText(/active \(1\)/);
    rerender(<RefinementList base="/" repo="/w" />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(RECONNECTING)).toBeNull();
  });
});

describe("run detail in every state its fetch can be in", () => {
  it("draws its loading state until the fetch answers", () => {
    serve("never");
    render(<RunDetail base="/" repo="/w" runId="r1" onClose={vi.fn()} />);
    expect(screen.getByText(LOADING)).not.toBeNull();
  });

  it("draws the run once it has it", async () => {
    const set = serve("ok");
    set(runDetail());
    render(<RunDetail base="/" repo="/w" runId="r1" onClose={vi.fn()} />);
    expect(await screen.findByText(/walk the changed pairs/)).not.toBeNull();
  });

  it("a failed fetch is an error with a retry that is not the close control", async () => {
    serve("down");
    const onClose = vi.fn();
    render(<RunDetail base="/" repo="/w" runId="r1" onClose={onClose} />);
    expect((await screen.findByRole("alert")).textContent).toContain(FAILED);
    expect(screen.getByRole("button", { name: "Retry" })).not.toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("a second row opened under a dead daemon is a first-load failure, not a reconnect", async () => {
    const set = serve("ok", "down");
    set(runDetail());
    render(<RunStream live={graph(received(initial(null), runsView({ runs: TWO })) as never)} />);
    const [first, other] = screen.getAllByRole("row").slice(1);
    fireEvent.click(first);
    await screen.findByText(/walk the changed pairs/);
    fireEvent.click(other);
    expect((await screen.findByRole("alert")).textContent).toContain(FAILED);
    expect(screen.queryByText(RECONNECTING)).toBeNull();
    expect(screen.queryByText(/walk the changed pairs/)).toBeNull();
  });
});

describe("a request the daemon refused, which is not an outage", () => {
  it("the run stream keeps its rows and offers no retry that could never succeed", () => {
    serve("never");
    const ready = received(initial(runsView()), runsView());
    const state = failed(ready, "limit must be a whole number, not 'abc'", true);
    render(<RunStream live={graph(state as never)} />);
    const box = screen.getByRole("alert");
    expect(box.textContent).toContain(REFUSED);
    expect(box.textContent).toContain("limit must be a whole number, not 'abc'");
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
    expect(screen.getAllByRole("row").length).toBeGreaterThan(1);
  });

  it("a 400 on a panel's own fetch reads as a refusal, never as a reconnect", async () => {
    declines(400, "no repo named that");
    render(<RefinementList base="/" repo="/w" />);
    expect((await screen.findByRole("alert")).textContent).toContain(REFUSED);
    expect(screen.queryByText(RECONNECTING)).toBeNull();
    expect(screen.queryByText(FAILED)).toBeNull();
  });

  it("a 503 is still an outage with a retry, so the two are not the same box", async () => {
    declines(503, "the index is rebuilding");
    render(<RefinementList base="/" repo="/w" />);
    expect((await screen.findByRole("alert")).textContent).toContain(FAILED);
    expect(screen.getByRole("button", { name: "Retry" })).not.toBeNull();
  });

  it("run detail says which request was refused rather than blaming the connection", async () => {
    declines(404, "no run r1 in this repo's ledger");
    render(<RunDetail base="/" repo="/w" runId="r1" onClose={vi.fn()} />);
    const box = await screen.findByRole("alert");
    expect(box.textContent).toContain(REFUSED);
    expect(box.textContent).toContain("no run r1 in this repo's ledger");
  });

  it("a refused flow walk says so under the controls that would change it", async () => {
    declines(400, "symbol must not be empty");
    render(<FlowPanel base="/" repo="/w" />);
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "build" } });
    expect((await screen.findByRole("alert")).textContent).toContain(REFUSED);
    expect(screen.getByLabelText("Depth")).not.toBeNull();
  });
});

describe("the flow panel in every state its fetch can be in", () => {
  const walk = () =>
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "build" } });

  it("says what it is waiting for before a symbol is typed", () => {
    serve("never");
    render(<FlowPanel base="/" repo="/w" />);
    expect(screen.getByText("No flow yet")).not.toBeNull();
  });

  it("draws its loading state while the walk is out", async () => {
    serve("never");
    render(<FlowPanel base="/" repo="/w" />);
    walk();
    expect(await screen.findByText(LOADING)).not.toBeNull();
  });

  it("draws the walk once it has one", async () => {
    const set = serve("ok");
    set(flowView());
    render(<FlowPanel base="/" repo="/w" />);
    walk();
    expect(await screen.findByTitle("app/cli.py::main")).not.toBeNull();
  });

  it("a failed walk is an error with a retry, never a blank panel", async () => {
    serve("down");
    render(<FlowPanel base="/" repo="/w" />);
    walk();
    expect((await screen.findByRole("alert")).textContent).toContain(FAILED);
  });

  it("a failed refetch over a symbol the graph does not hold still says it holds none", async () => {
    const set = serve("ok", "down");
    set(flowView({ symbol: "build", flow: null }));
    render(<FlowPanel base="/" repo="/w" />);
    walk();
    await screen.findByText("No flow for that symbol yet");
    fireEvent.click(screen.getByRole("button", { name: "in" }));
    expect(await screen.findByText(RECONNECTING)).not.toBeNull();
    expect(screen.getByText("No flow for that symbol yet")).not.toBeNull();
  });

  it("a failed refetch keeps the last walk under a reconnect banner", async () => {
    const set = serve("ok", "down");
    set(flowView());
    render(<FlowPanel base="/" repo="/w" />);
    walk();
    await screen.findByTitle("app/cli.py::main");
    fireEvent.click(screen.getByRole("button", { name: "in" }));
    expect(await screen.findByText(RECONNECTING)).not.toBeNull();
    expect(screen.getByTitle("app/cli.py::main")).not.toBeNull();
  });
});
