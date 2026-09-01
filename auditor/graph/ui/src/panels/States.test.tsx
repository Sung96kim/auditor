import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  answered,
  Empty,
  failing,
  Failed,
  Loading,
  Phases,
  Reconnecting,
  Refused,
  reason,
} from "./States";
import { failed, initial, received } from "../api/poll";

afterEach(cleanup);

describe("the four states every polled surface renders through", () => {
  it("an empty ledger says so and is not an error", () => {
    render(<Empty what="runs" hint="the observer has not started a run for this repo yet" />);
    expect(screen.getByRole("status").textContent).toContain("No runs yet");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("loading names what it is waiting for, so no panel is a bare spinner", () => {
    render(<Loading what="the daemon" />);
    expect(screen.getByRole("status").textContent).toContain("Loading the daemon");
  });

  it("a reconnect carries the error and a retry that fires, never a blank panel", () => {
    const onRetry = vi.fn();
    render(<Reconnecting error="network down" onRetry={onRetry} />);
    expect(screen.getByRole("status").textContent).toContain("network down");
    fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("a failed first poll is an alert with a retry, which is what ends the spinner", () => {
    const onRetry = vi.fn();
    render(<Failed error="network down" onRetry={onRetry} />);
    expect(screen.getByRole("alert").textContent).toContain("Could not reach the observer");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("a request the daemon answered and declined", () => {
  it("names the request rather than the connection, and offers no retry that cannot work", () => {
    render(<Refused error="Error: limit must be a whole number, not 'abc'" />);
    const box = screen.getByRole("alert");
    expect(box.textContent).toContain("The observer refused this request");
    expect(box.textContent).toContain("limit must be a whole number, not 'abc'");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("does not borrow the reconnect's words, which promise an outage that will pass", () => {
    render(<Refused error="no repo named that" />);
    expect(screen.getByRole("alert").textContent).not.toContain("Reconnecting");
  });
});

describe("what a state box looks like, not only what it says", () => {
  it("the thrown class name never reaches the reader, only the reason does", () => {
    expect(reason("Error: connection refused")).toBe("connection refused");
    expect(reason("TypeError: fetch failed")).toBe("fetch failed");
    expect(reason("connection refused")).toBe("connection refused");
  });

  it("a retry is a styled control, never the browser's own grey button on a dark panel", () => {
    render(<Failed error="Error: network down" onRetry={vi.fn()} />);
    const button = screen.getByRole("button", { name: "Retry" });
    expect(button.style.border).not.toBe("");
    expect(button.style.color).not.toBe("");
    expect(button.style.cursor).toBe("pointer");
  });

  it("a failure and a reconnect are told apart by tone, not only by wording", () => {
    const { container: bad } = render(<Failed error="down" onRetry={vi.fn()} />);
    const { container: warn } = render(<Reconnecting error="down" onRetry={vi.fn()} />);
    const wash = (el: Element) => (el.firstElementChild as HTMLElement).style.background;
    expect(wash(bad)).not.toBe("");
    expect(wash(bad)).not.toBe(wash(warn));
  });

  it("an empty ledger separates its answer from its hint, so the two do not read as one line", () => {
    render(<Empty what="runs" hint="the observer has not started a run yet" />);
    const box = screen.getByRole("status");
    expect(box.children.length).toBe(2);
    expect(box.children[0].textContent).toBe("No runs yet");
  });

  it("loading shows a pending indicator, so a slow poll is not a line of text alone", () => {
    const { container } = render(<Loading what="the daemon" />);
    expect(container.querySelector(".state-track")).not.toBeNull();
  });
});

const READY = received(initial<number>(), 1);

describe("the ladder from a phase to its box, which six panels used to spell themselves", () => {
  it("a one-shot surface handed a stale state draws nothing, so the flag is not a no-op", () => {
    const { container } = render(
      <Phases state={failed(READY, "gone")} what="runs" onRetry={vi.fn()} reconnects={false} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("the same state on a polled surface is the reconnect banner", () => {
    render(<Phases state={failed(READY, "gone")} what="runs" onRetry={vi.fn()} />);
    expect(screen.getByText("Reconnecting to the observer")).not.toBeNull();
  });

  it("a first poll that is still out names what it is waiting for", () => {
    render(<Phases state={initial<number>()} what="the evals" onRetry={vi.fn()} />);
    expect(screen.getByText("Loading the evals")).not.toBeNull();
  });

  it("a refusal is drawn whether or not the surface reconnects, because it is the answer", () => {
    render(
      <Phases
        state={failed(READY, "no repo named that", true)}
        what="runs"
        onRetry={vi.fn()}
        reconnects={false}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("The observer refused this request");
  });

  it("an answer a failing refetch sits on is still an answer, and a refusal is not one", () => {
    expect(answered(READY)).toBe(true);
    expect(answered(failed(READY, "gone"))).toBe(true);
    expect(answered(initial<number>())).toBe(false);
    expect(answered(failed(initial<number>(), "gone"))).toBe(false);
    expect(answered(failed(READY, "no repo named that", true))).toBe(false);
  });

  it("only the two failure phases are failing, which is the guard Chrome keeps", () => {
    expect(failing(failed(READY, "no repo named that", true))).toBe(true);
    expect(failing(failed(initial<number>(), "gone"))).toBe(true);
    expect(failing(failed(READY, "gone"))).toBe(false);
    expect(failing(READY)).toBe(false);
    expect(failing(initial<number>())).toBe(false);
  });
});
