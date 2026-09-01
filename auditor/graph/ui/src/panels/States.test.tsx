import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Empty, Failed, Loading, Reconnecting, Refused, reason } from "./States";

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
